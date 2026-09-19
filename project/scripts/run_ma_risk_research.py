"""移動平均戦略へ損切り・利確・保有期限を加えて比較する。"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple, cast

from services.ma_research_service import (
    DATETIME_FORMAT,
    Direction,
    ResearchPanel,
    ResearchStrategy,
    build_entry_rules,
    build_exit_rules,
    build_strategies,
    prepare_research_panel,
    simulate_strategy,
)


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.strptime(value, DATETIME_FORMAT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "日時は YYYY.MM.DD HH:MM:SS 形式で指定してください"
        ) from exc


def _parse_periods(value: str) -> Tuple[int, int, int]:
    periods = tuple(int(item.strip()) for item in value.split(","))
    if len(periods) != 3 or not periods[0] < periods[1] < periods[2]:
        raise argparse.ArgumentTypeError("短期 < 中期 < 長期の3つを指定してください")
    return cast(Tuple[int, int, int], periods)


def _parse_float_values(value: str) -> Tuple[float, ...]:
    try:
        values = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "カンマ区切りの数値で指定してください"
        ) from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("0より大きい数値を指定してください")
    return values


def _parse_int_values(value: str) -> Tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "カンマ区切りの整数で指定してください"
        ) from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("1以上の整数を指定してください")
    return values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="移動平均戦略の損切り・利確・保有期限を比較します。"
    )
    parser.add_argument("--input", required=True, metavar="PATH")
    parser.add_argument("--output", required=True, metavar="PATH")
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--direction", choices=("long", "short"), required=True)
    parser.add_argument("--ma-periods", type=_parse_periods, required=True)
    parser.add_argument("--scan-from", type=_parse_datetime, required=True)
    parser.add_argument("--scan-to", type=_parse_datetime, required=True)
    parser.add_argument("--one-year-from", type=_parse_datetime, required=True)
    parser.add_argument("--six-month-from", type=_parse_datetime, required=True)
    parser.add_argument(
        "--risk-values",
        type=_parse_float_values,
        default=(5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0, 80.0, 120.0),
    )
    parser.add_argument(
        "--holding-values",
        type=_parse_int_values,
        default=(240, 480, 720, 1440, 2880, 4320, 10080),
    )
    parser.add_argument(
        "--trailing-percent-values",
        type=_parse_float_values,
        default=(
            0.25,
            0.5,
            0.75,
            1.0,
            1.25,
            1.5,
            1.75,
            2.0,
            2.5,
            3.0,
            4.0,
            5.0,
        ),
    )
    parser.add_argument("--point-size", type=float, default=0.01)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _read_payload(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as source:
        payload = json.load(source)
    if not isinstance(payload, Mapping):
        raise ValueError("入力JSONの最上位はオブジェクトで指定してください")
    return cast(Mapping[str, object], payload)


def _read_datasets(payload: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    datasets = payload.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("datasets は1件以上の配列で指定してください")
    return tuple(cast(Mapping[str, object], dataset) for dataset in datasets)


def _period_ranges(
    panel: ResearchPanel,
    one_year_from: datetime,
    six_month_from: datetime,
) -> Mapping[str, Tuple[datetime, datetime]]:
    return {
        "overall": (panel.effective_from, panel.effective_to),
        "one_year": (max(panel.effective_from, one_year_from), panel.effective_to),
        "six_month": (max(panel.effective_from, six_month_from), panel.effective_to),
    }


def _find_strategy(
    panel: ResearchPanel,
    direction: Direction,
    strategy_id: str,
) -> ResearchStrategy:
    strategies = build_strategies(
        entry_rules=build_entry_rules(panel, direction),
        exit_rules=build_exit_rules(panel, direction),
    )
    try:
        return next(item for item in strategies if item.strategy_id == strategy_id)
    except StopIteration as exc:
        raise ValueError(f"戦略IDが見つかりません: {strategy_id}") from exc


def _build_variants(
    risk_values: Sequence[float],
    holding_values: Sequence[int],
    trailing_percent_values: Sequence[float],
) -> Sequence[Mapping[str, object]]:
    variants: List[Mapping[str, object]] = [
        {
            "variant_id": "base",
            "stop_loss": None,
            "take_profit": None,
            "trailing_stop": None,
            "trailing_stop_pct": None,
            "maximum_holding_minutes": None,
        }
    ]

    def append(
        variant_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        trailing_stop_pct: Optional[float] = None,
        maximum_holding_minutes: Optional[int] = None,
    ) -> None:
        variants.append(
            {
                "variant_id": variant_id,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "trailing_stop": trailing_stop,
                "trailing_stop_pct": trailing_stop_pct,
                "maximum_holding_minutes": maximum_holding_minutes,
            }
        )

    for value in risk_values:
        append(f"sl_{value:g}", stop_loss=value)
        append(f"tp_{value:g}", take_profit=value)
        append(f"trailing_{value:g}", trailing_stop=value)
    for minutes in holding_values:
        append(f"hold_{minutes}", maximum_holding_minutes=minutes)
    for percent in trailing_percent_values:
        append(
            f"trailing_pct_{percent:g}",
            trailing_stop_pct=percent,
        )
        for value in risk_values:
            append(
                f"trailing_pct_{percent:g}__tp_{value:g}",
                trailing_stop_pct=percent,
                take_profit=value,
            )
        for minutes in holding_values:
            append(
                f"trailing_pct_{percent:g}__hold_{minutes}",
                trailing_stop_pct=percent,
                maximum_holding_minutes=minutes,
            )
    for first in risk_values:
        for second in risk_values:
            append(
                f"sl_{first:g}__tp_{second:g}",
                stop_loss=first,
                take_profit=second,
            )
            append(
                f"trailing_{first:g}__tp_{second:g}",
                trailing_stop=first,
                take_profit=second,
            )
        for minutes in holding_values:
            append(
                f"sl_{first:g}__hold_{minutes}",
                stop_loss=first,
                maximum_holding_minutes=minutes,
            )
            append(
                f"trailing_{first:g}__hold_{minutes}",
                trailing_stop=first,
                maximum_holding_minutes=minutes,
            )
    return tuple(variants)


def _evaluate_variant(
    panel: ResearchPanel,
    strategy: ResearchStrategy,
    variant: Mapping[str, object],
    ranges: Mapping[str, Tuple[datetime, datetime]],
    point_size: float,
) -> dict[str, object]:
    return {
        **variant,
        "periods": {
            name: dict(
                simulate_strategy(
                    panel=panel,
                    strategy=strategy,
                    scan_from=start,
                    scan_to=end,
                    point_size=point_size,
                    stop_loss=cast(Optional[float], variant["stop_loss"]),
                    take_profit=cast(Optional[float], variant["take_profit"]),
                    trailing_stop=cast(Optional[float], variant["trailing_stop"]),
                    trailing_stop_pct=cast(
                        Optional[float], variant["trailing_stop_pct"]
                    ),
                    maximum_holding_minutes=cast(
                        Optional[int], variant["maximum_holding_minutes"]
                    ),
                ).metrics
            )
            for name, (start, end) in ranges.items()
        },
    }


def _metric(
    item: Mapping[str, object],
    period: str,
    name: str,
) -> float:
    periods = cast(Mapping[str, object], item["periods"])
    period_value = cast(Mapping[str, object], periods[period])
    adjusted = cast(Mapping[str, object], period_value["spread_adjusted"])
    value = adjusted[name]
    if value is None:
        return float("-inf")
    return float(cast(float, value))


def _rank(results: Sequence[dict[str, object]]) -> None:
    for period in ("overall", "one_year", "six_month"):
        for metric_name, rank_name in (
            ("total_profit", "profit_rank"),
            ("recovery_factor", "recovery_rank"),
        ):
            ordered = sorted(
                results,
                key=lambda item: (
                    -_metric(item, period, metric_name),
                    _metric(item, period, "maximum_drawdown"),
                    str(item["variant_id"]),
                ),
            )
            previous: Optional[Tuple[float, float]] = None
            dense_rank = 0
            for item in ordered:
                current = (
                    _metric(item, period, metric_name),
                    _metric(item, period, "maximum_drawdown"),
                )
                if current != previous:
                    dense_rank += 1
                    previous = current
                periods = cast(dict[str, object], item["periods"])
                value = cast(dict[str, object], periods[period])
                value[rank_name] = dense_rank

    for item in results:
        item_periods = cast(Mapping[str, object], item["periods"])
        eligible = all(
            cast(
                int,
                cast(Mapping[str, object], item_periods[period])["trade_count"],
            )
            >= 10
            and _metric(item, period, "total_profit") > 0
            for period in ("overall", "one_year", "six_month")
        )
        item["robust_eligible"] = eligible
        item["balanced_rank_sum"] = sum(
            cast(
                int,
                cast(Mapping[str, object], item_periods[period])[rank_name],
            )
            for period in ("overall", "one_year", "six_month")
            for rank_name in ("profit_rank", "recovery_rank")
        )


def _write_json(path: Path, payload: Mapping[str, object], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ValueError(f"出力先が既に存在します: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2, allow_nan=False)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        input_path = Path(args.input)
        payload = _read_payload(input_path)
        panel = prepare_research_panel(
            datasets=_read_datasets(payload),
            moving_average_periods=args.ma_periods,
            scan_from=args.scan_from,
            scan_to=args.scan_to,
        )
        direction = cast(Direction, args.direction)
        strategy = _find_strategy(panel, direction, args.strategy_id)
        ranges = _period_ranges(panel, args.one_year_from, args.six_month_from)
        variants = _build_variants(
            args.risk_values,
            args.holding_values,
            args.trailing_percent_values,
        )
        results = [
            _evaluate_variant(
                panel=panel,
                strategy=strategy,
                variant=variant,
                ranges=ranges,
                point_size=args.point_size,
            )
            for variant in variants
        ]
        _rank(results)
        results.sort(
            key=lambda item: (
                not bool(item["robust_eligible"]),
                cast(int, item["balanced_rank_sum"]),
                -_metric(item, "overall", "total_profit"),
            )
        )
        output = {
            "schema_version": "1.0",
            "input_path": str(input_path),
            "settings": {
                "symbol": panel.symbol,
                "moving_average_periods": list(panel.moving_average_periods),
                "effective_from": panel.effective_from.strftime(DATETIME_FORMAT),
                "effective_to": panel.effective_to.strftime(DATETIME_FORMAT),
                "period_ranges": {
                    name: {
                        "from": start.strftime(DATETIME_FORMAT),
                        "to": end.strftime(DATETIME_FORMAT),
                    }
                    for name, (start, end) in ranges.items()
                },
                "risk_values": list(args.risk_values),
                "holding_values": list(args.holding_values),
                "trailing_percent_values": list(args.trailing_percent_values),
                "intrabar_policy": (
                    "bar open gap first; otherwise stop before take-profit; "
                    "trailing updated after the bar"
                ),
                "costs": (
                    "recorded spread included; commission, slippage and swap excluded"
                ),
                "actual_trading": False,
            },
            "strategy": {
                "strategy_id": strategy.strategy_id,
                "direction": strategy.direction,
                "entry_rule": strategy.entry_rule.label,
                "exit_rule": strategy.exit_rule.label,
            },
            "variant_count": len(variants),
            "ranking": (
                "positive spread-adjusted profit and at least 10 trades in all "
                "periods; sum of profit and recovery-factor ranks"
            ),
            "variants": results,
        }
        _write_json(Path(args.output), output, args.overwrite)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
