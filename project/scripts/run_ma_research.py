"""複数時間足の移動平均条件を探索的に比較する。"""

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
    try:
        periods = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "移動平均の期間はカンマ区切りの整数で指定してください"
        ) from exc
    if len(periods) != 3 or not periods[0] < periods[1] < periods[2]:
        raise argparse.ArgumentTypeError(
            "移動平均の期間は短期 < 中期 < 長期の3つを指定してください"
        )
    return cast(Tuple[int, int, int], periods)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="複数時間足の移動平均エントリー・決済条件を比較します。"
    )
    parser.add_argument("--input", required=True, metavar="PATH")
    parser.add_argument("--output", required=True, metavar="PATH")
    parser.add_argument(
        "--trades-output",
        metavar="PATH",
        help="推奨候補と基準戦略の取引明細出力先",
    )
    parser.add_argument(
        "--ma-periods",
        type=_parse_periods,
        default=(5, 20, 60),
        metavar="PERIODS",
    )
    parser.add_argument(
        "--scan-from",
        type=_parse_datetime,
        default=datetime(2024, 8, 26),
        metavar="DATETIME",
    )
    parser.add_argument(
        "--scan-to",
        type=_parse_datetime,
        default=datetime(2026, 8, 26),
        metavar="DATETIME",
    )
    parser.add_argument(
        "--one-year-from",
        type=_parse_datetime,
        default=datetime(2025, 8, 25, 20),
        metavar="DATETIME",
    )
    parser.add_argument(
        "--six-month-from",
        type=_parse_datetime,
        default=datetime(2026, 2, 25, 20),
        metavar="DATETIME",
    )
    parser.add_argument(
        "--directions",
        choices=("long", "short", "both"),
        default="both",
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
    result = []
    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, Mapping):
            raise ValueError(f"datasets[{index}] はオブジェクトで指定してください")
        result.append(cast(Mapping[str, object], dataset))
    return tuple(result)


def _period_ranges(
    effective_from: datetime,
    effective_to: datetime,
    one_year_from: datetime,
    six_month_from: datetime,
) -> Mapping[str, Tuple[datetime, datetime]]:
    ranges = {
        "overall": (effective_from, effective_to),
        "one_year": (max(effective_from, one_year_from), effective_to),
        "six_month": (max(effective_from, six_month_from), effective_to),
    }
    if any(start >= end for start, end in ranges.values()):
        raise ValueError("期間別検証に使用できる共通期間がありません")
    return ranges


def _rank_strategy_results(
    results: List[dict[str, object]],
) -> None:
    for period_name in ("overall", "one_year", "six_month"):
        for metric_name, rank_key in (
            ("gross", "gross_rank"),
            ("spread_adjusted", "spread_adjusted_rank"),
        ):
            ranked = sorted(
                results,
                key=lambda item: (
                    -_total_profit(item, period_name, metric_name),
                    _maximum_drawdown(item, period_name, metric_name),
                    str(item["strategy_id"]),
                ),
            )
            previous_key: Optional[Tuple[float, float]] = None
            dense_rank = 0
            for item in ranked:
                current_key = (
                    _total_profit(item, period_name, metric_name),
                    _maximum_drawdown(item, period_name, metric_name),
                )
                if current_key != previous_key:
                    dense_rank += 1
                    previous_key = current_key
                periods = cast(dict[str, object], item["periods"])
                period = cast(dict[str, object], periods[period_name])
                period[rank_key] = dense_rank

    for item in results:
        periods = cast(dict[str, object], item["periods"])
        period_items = [
            cast(dict[str, object], periods[name])
            for name in ("overall", "one_year", "six_month")
        ]
        eligible = all(
            cast(
                float,
                cast(dict[str, object], period["spread_adjusted"])["total_profit"],
            )
            > 0
            and cast(int, period["trade_count"]) >= 10
            for period in period_items
        )
        item["robust_eligible"] = eligible
        item["spread_adjusted_rank_sum"] = sum(
            cast(int, period["spread_adjusted_rank"]) for period in period_items
        )

    robust = sorted(
        (item for item in results if item["robust_eligible"]),
        key=lambda item: (
            cast(int, item["spread_adjusted_rank_sum"]),
            -_total_profit(item, "overall", "spread_adjusted"),
            str(item["strategy_id"]),
        ),
    )
    previous_robust_key: Optional[Tuple[int, float]] = None
    robust_rank = 0
    for item in robust:
        current_robust_key = (
            cast(int, item["spread_adjusted_rank_sum"]),
            _total_profit(item, "overall", "spread_adjusted"),
        )
        if current_robust_key != previous_robust_key:
            robust_rank += 1
            previous_robust_key = current_robust_key
        item["robust_rank"] = robust_rank
    for item in results:
        if "robust_rank" not in item:
            item["robust_rank"] = None


def _total_profit(
    item: Mapping[str, object],
    period_name: str,
    metric_name: str,
) -> float:
    periods = cast(Mapping[str, object], item["periods"])
    period = cast(Mapping[str, object], periods[period_name])
    metric = cast(Mapping[str, object], period[metric_name])
    return float(cast(float, metric["total_profit"]))


def _maximum_drawdown(
    item: Mapping[str, object],
    period_name: str,
    metric_name: str,
) -> float:
    periods = cast(Mapping[str, object], item["periods"])
    period = cast(Mapping[str, object], periods[period_name])
    metric = cast(Mapping[str, object], period[metric_name])
    return float(cast(float, metric["maximum_drawdown"]))


def _result_for_strategy(
    panel: ResearchPanel,
    strategy: ResearchStrategy,
    ranges: Mapping[str, Tuple[datetime, datetime]],
    point_size: float,
) -> dict[str, object]:
    return {
        "strategy_id": strategy.strategy_id,
        "direction": strategy.direction,
        "entry_rule_id": strategy.entry_rule.rule_id,
        "entry_rule": strategy.entry_rule.label,
        "exit_rule_id": strategy.exit_rule.rule_id,
        "exit_rule": strategy.exit_rule.label,
        "periods": {
            name: dict(
                simulate_strategy(
                    panel=panel,
                    strategy=strategy,
                    scan_from=start,
                    scan_to=end,
                    point_size=point_size,
                ).metrics
            )
            for name, (start, end) in ranges.items()
        },
    }


def _strategy_by_id(
    strategies: Sequence[ResearchStrategy],
    strategy_id: str,
) -> ResearchStrategy:
    return next(
        strategy for strategy in strategies if strategy.strategy_id == strategy_id
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
        output_path = Path(args.output)
        trades_output_path = Path(args.trades_output) if args.trades_output else None
        payload = _read_payload(input_path)
        datasets = _read_datasets(payload)
        panel = prepare_research_panel(
            datasets=datasets,
            moving_average_periods=args.ma_periods,
            scan_from=args.scan_from,
            scan_to=args.scan_to,
        )
        ranges = _period_ranges(
            effective_from=panel.effective_from,
            effective_to=panel.effective_to,
            one_year_from=args.one_year_from,
            six_month_from=args.six_month_from,
        )
        directions: Sequence[Direction] = (
            ("long", "short")
            if args.directions == "both"
            else (cast(Direction, args.directions),)
        )
        strategies: List[ResearchStrategy] = []
        for direction in directions:
            strategies.extend(
                build_strategies(
                    entry_rules=build_entry_rules(panel, direction),
                    exit_rules=build_exit_rules(panel, direction),
                )
            )
        results = [
            _result_for_strategy(
                panel=panel,
                strategy=strategy,
                ranges=ranges,
                point_size=args.point_size,
            )
            for strategy in strategies
        ]
        _rank_strategy_results(results)
        results.sort(
            key=lambda item: (
                item["robust_rank"] is None,
                item["robust_rank"] if item["robust_rank"] is not None else 10**9,
                -_total_profit(item, "overall", "spread_adjusted"),
            )
        )
        settings = {
            "symbol": panel.symbol,
            "moving_average_periods": list(panel.moving_average_periods),
            "timeframes": ["M5", "M15", "M30", "H1"],
            "effective_from": panel.effective_from.strftime(DATETIME_FORMAT),
            "effective_to": panel.effective_to.strftime(DATETIME_FORMAT),
            "period_ranges": {
                name: {
                    "from": start.strftime(DATETIME_FORMAT),
                    "to": end.strftime(DATETIME_FORMAT),
                }
                for name, (start, end) in ranges.items()
            },
            "directions": list(directions),
            "point_size": args.point_size,
            "costs": {
                "gross": "spread, commission, slippage, swap excluded",
                "spread_adjusted": (
                    "long entry or short exit adjusted by recorded spread"
                ),
                "excluded_from_spread_adjusted": [
                    "commission",
                    "slippage",
                    "swap",
                ],
            },
            "ranking": (
                "positive spread-adjusted profit and at least 10 trades in all "
                "three periods; ascending sum of three spread-adjusted ranks"
            ),
            "position_policy": "single position per strategy",
            "actual_trading": False,
        }
        output = {
            "schema_version": "1.0",
            "input_path": str(input_path),
            "settings": settings,
            "entry_rule_count": sum(
                len(build_entry_rules(panel, direction)) for direction in directions
            ),
            "exit_rule_count": sum(
                len(build_exit_rules(panel, direction)) for direction in directions
            ),
            "strategy_count": len(strategies),
            "robust_eligible_count": sum(
                bool(item["robust_eligible"]) for item in results
            ),
            "strategies": results,
        }
        _write_json(output_path, output, args.overwrite)

        if trades_output_path is not None:
            selected_ids = [
                cast(str, item["strategy_id"])
                for item in results
                if item["robust_rank"] in (1, 2, 3)
            ]
            baseline_ids = [
                strategy.strategy_id
                for strategy in strategies
                if strategy.entry_rule.rule_id == "validation1_M30"
                and strategy.exit_rule.rule_id == "cross_M30_5_60"
            ]
            selected_ids.extend(
                strategy_id
                for strategy_id in baseline_ids
                if strategy_id not in selected_ids
            )
            trade_output = {
                "schema_version": "1.0",
                "settings": settings,
                "strategies": [
                    {
                        "strategy_id": strategy_id,
                        "trades": simulate_strategy(
                            panel=panel,
                            strategy=_strategy_by_id(strategies, strategy_id),
                            scan_from=ranges["overall"][0],
                            scan_to=ranges["overall"][1],
                            point_size=args.point_size,
                            collect_trades=True,
                        ).trades,
                    }
                    for strategy_id in selected_ids
                ],
            }
            _write_json(trades_output_path, trade_output, args.overwrite)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
