"""ロングとショートの併用方法を比較する。"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import List, Mapping, Optional, Sequence, Tuple, cast

from services.ma_research_service import (
    DATETIME_FORMAT,
    Direction,
    ResearchPanel,
    ResearchStrategy,
    StrategySimulationSpec,
    build_entry_rules,
    build_exit_rules,
    build_strategies,
    prepare_research_panel,
    simulate_single_position_strategies,
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
            "移動平均期間はカンマ区切りの整数で指定してください"
        ) from exc
    if len(periods) != 3 or not periods[0] < periods[1] < periods[2]:
        raise argparse.ArgumentTypeError("短期 < 中期 < 長期の3つを指定してください")
    return cast(Tuple[int, int, int], periods)


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("0より大きい数値を指定してください")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("1以上の整数を指定してください")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ロングとショートの独立保有・1ポジション限定を比較します。"
    )
    parser.add_argument("--input", required=True, metavar="PATH")
    parser.add_argument("--output", required=True, metavar="PATH")
    parser.add_argument("--scan-from", type=_parse_datetime, required=True)
    parser.add_argument("--scan-to", type=_parse_datetime, required=True)
    parser.add_argument("--one-year-from", type=_parse_datetime, required=True)
    parser.add_argument("--six-month-from", type=_parse_datetime, required=True)
    parser.add_argument("--long-ma-periods", type=_parse_periods, required=True)
    parser.add_argument("--long-strategy-id", required=True)
    parser.add_argument("--long-stop-loss", type=_positive_float)
    parser.add_argument("--long-take-profit", type=_positive_float)
    parser.add_argument("--long-trailing-stop", type=_positive_float)
    parser.add_argument("--long-maximum-holding", type=_positive_int)
    parser.add_argument("--short-ma-periods", type=_parse_periods, required=True)
    parser.add_argument("--short-strategy-id", required=True)
    parser.add_argument("--short-stop-loss", type=_positive_float)
    parser.add_argument("--short-take-profit", type=_positive_float)
    parser.add_argument("--short-trailing-stop", type=_positive_float)
    parser.add_argument("--short-maximum-holding", type=_positive_int)
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


def _ranges(
    panel: ResearchPanel,
    one_year_from: datetime,
    six_month_from: datetime,
) -> Mapping[str, Tuple[datetime, datetime]]:
    return {
        "overall": (panel.effective_from, panel.effective_to),
        "one_year": (max(panel.effective_from, one_year_from), panel.effective_to),
        "six_month": (max(panel.effective_from, six_month_from), panel.effective_to),
    }


def _round(value: float) -> float:
    return round(float(value), 8)


def _profit_metrics(profits: Sequence[float]) -> Mapping[str, object]:
    wins = [profit for profit in profits if profit > 0]
    losses = [profit for profit in profits if profit < 0]
    cumulative = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for profit in profits:
        cumulative += profit
        peak = max(peak, cumulative)
        maximum_drawdown = max(maximum_drawdown, peak - cumulative)
    total_profit = sum(profits)
    profit_factor = sum(wins) / abs(sum(losses)) if losses else None
    recovery_factor = total_profit / maximum_drawdown if maximum_drawdown > 0 else None
    return {
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": len(profits) - len(wins) - len(losses),
        "win_rate_pct": _round(len(wins) / len(profits) * 100) if profits else 0.0,
        "total_profit": _round(total_profit),
        "average_profit": _round(mean(profits)) if profits else None,
        "profit_factor": _round(profit_factor) if profit_factor is not None else None,
        "maximum_drawdown": _round(maximum_drawdown),
        "recovery_factor": (
            _round(recovery_factor) if recovery_factor is not None else None
        ),
    }


def _independent_metrics(
    trades: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    ordered = sorted(
        trades,
        key=lambda trade: (
            str(trade["exit_time"]),
            str(trade["direction"]),
            int(cast(int, trade["sequence"])),
        ),
    )
    gross = [float(cast(float, trade["gross_profit"])) for trade in ordered]
    spread_adjusted = [
        float(cast(float, trade["spread_adjusted_profit"])) for trade in ordered
    ]
    holding = [int(cast(int, trade["holding_minutes"])) for trade in ordered]
    return {
        "trade_count": len(ordered),
        "forced_close_count": sum(bool(trade["forced"]) for trade in ordered),
        "average_holding_minutes": _round(mean(holding)) if holding else None,
        "maximum_holding_minutes": max(holding) if holding else 0,
        "gross": _profit_metrics(gross),
        "spread_adjusted": _profit_metrics(spread_adjusted),
    }


def _strategy_trades(
    specification: StrategySimulationSpec,
    scan_from: datetime,
    scan_to: datetime,
    point_size: float,
) -> Sequence[Mapping[str, object]]:
    result = simulate_strategy(
        panel=specification.panel,
        strategy=specification.strategy,
        scan_from=scan_from,
        scan_to=scan_to,
        point_size=point_size,
        collect_trades=True,
        stop_loss=specification.stop_loss,
        take_profit=specification.take_profit,
        trailing_stop=specification.trailing_stop,
        trailing_stop_pct=specification.trailing_stop_pct,
        maximum_holding_minutes=specification.maximum_holding_minutes,
    )
    return tuple(
        {
            **trade,
            "strategy_id": specification.strategy.strategy_id,
            "direction": specification.strategy.direction,
        }
        for trade in result.trades
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


def _specification(
    panel: ResearchPanel,
    direction: Direction,
    strategy_id: str,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    trailing_stop: Optional[float],
    maximum_holding_minutes: Optional[int],
) -> StrategySimulationSpec:
    return StrategySimulationSpec(
        panel=panel,
        strategy=_find_strategy(panel, direction, strategy_id),
        stop_loss=stop_loss,
        take_profit=take_profit,
        trailing_stop=trailing_stop,
        maximum_holding_minutes=maximum_holding_minutes,
    )


def _describe(specification: StrategySimulationSpec) -> Mapping[str, object]:
    return {
        "strategy_id": specification.strategy.strategy_id,
        "direction": specification.strategy.direction,
        "moving_average_periods": list(specification.panel.moving_average_periods),
        "entry_rule": specification.strategy.entry_rule.label,
        "exit_rule": specification.strategy.exit_rule.label,
        "stop_loss": specification.stop_loss,
        "take_profit": specification.take_profit,
        "trailing_stop": specification.trailing_stop,
        "trailing_stop_pct": specification.trailing_stop_pct,
        "maximum_holding_minutes": specification.maximum_holding_minutes,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        input_path = Path(args.input)
        datasets = _read_datasets(_read_payload(input_path))
        long_panel = prepare_research_panel(
            datasets=datasets,
            moving_average_periods=args.long_ma_periods,
            scan_from=args.scan_from,
            scan_to=args.scan_to,
        )
        short_panel = prepare_research_panel(
            datasets=datasets,
            moving_average_periods=args.short_ma_periods,
            scan_from=args.scan_from,
            scan_to=args.scan_to,
        )
        long_specification = _specification(
            panel=long_panel,
            direction="long",
            strategy_id=args.long_strategy_id,
            stop_loss=args.long_stop_loss,
            take_profit=args.long_take_profit,
            trailing_stop=args.long_trailing_stop,
            maximum_holding_minutes=args.long_maximum_holding,
        )
        short_specification = _specification(
            panel=short_panel,
            direction="short",
            strategy_id=args.short_strategy_id,
            stop_loss=args.short_stop_loss,
            take_profit=args.short_take_profit,
            trailing_stop=args.short_trailing_stop,
            maximum_holding_minutes=args.short_maximum_holding,
        )
        ranges = _ranges(long_panel, args.one_year_from, args.six_month_from)
        if _ranges(short_panel, args.one_year_from, args.six_month_from) != ranges:
            raise ValueError("ロングとショートの検証期間が一致していません")

        results = {}
        for name, (start, end) in ranges.items():
            single = simulate_single_position_strategies(
                specifications=(long_specification, short_specification),
                scan_from=start,
                scan_to=end,
                point_size=args.point_size,
            )
            independent_trades = (
                *_strategy_trades(long_specification, start, end, args.point_size),
                *_strategy_trades(short_specification, start, end, args.point_size),
            )
            results[name] = {
                "single_position": dict(single.metrics),
                "independent_positions": dict(_independent_metrics(independent_trades)),
            }

        output = {
            "schema_version": "1.0",
            "input_path": str(input_path),
            "settings": {
                "symbol": long_panel.symbol,
                "effective_from": long_panel.effective_from.strftime(DATETIME_FORMAT),
                "effective_to": long_panel.effective_to.strftime(DATETIME_FORMAT),
                "period_ranges": {
                    name: {
                        "from": start.strftime(DATETIME_FORMAT),
                        "to": end.strftime(DATETIME_FORMAT),
                    }
                    for name, (start, end) in ranges.items()
                },
                "single_position_simultaneous_entry": "skip both signals",
                "costs": (
                    "recorded spread included; commission, slippage and swap excluded"
                ),
                "actual_trading": False,
            },
            "long": _describe(long_specification),
            "short": _describe(short_specification),
            "periods": results,
        }
        _write_json(Path(args.output), output, args.overwrite)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
