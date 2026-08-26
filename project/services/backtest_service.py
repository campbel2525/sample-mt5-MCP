from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from statistics import mean, median
from typing import Dict, Mapping, Optional, Sequence, Tuple, cast

from services.chart_service import (
    MarketBar,
    TechnicalChart,
    normalize_applied_price,
    normalize_moving_average_method,
)
from services.cross_scan_service import CrossScanResult, MovingAverageCross

DATETIME_FORMAT = "%Y.%m.%d %H:%M:%S"
REQUIRED_PERIODS = ("M5", "M15", "M30")
PERIOD_MINUTES = {"M5": 5, "M15": 15, "M30": 30}
DEFAULT_PROFIT_TARGETS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0)


@dataclass(frozen=True)
class PreparedDataset:
    symbol: str
    period: str
    bars: Tuple[MarketBar, ...]
    usable_bars: Tuple[MarketBar, ...]
    times: Tuple[datetime, ...]
    index_by_time: Mapping[datetime, int]


@dataclass(frozen=True)
class ExitStrategy:
    strategy_id: str
    death_period: Optional[str]
    comparison_period: Optional[int]
    profit_target_pct: Optional[float]


@dataclass(frozen=True)
class EntryCandidate:
    signal_bar_time: datetime
    signal_available_time: datetime
    execution_time: datetime
    execution_price: float
    spread_points: int


@dataclass(frozen=True)
class ExitCandidate:
    signal_bar_time: Optional[datetime]
    signal_available_time: Optional[datetime]
    execution_time: datetime
    execution_price: float
    reason: str
    bar_time: datetime
    forced: bool = False


@dataclass(frozen=True)
class BacktestOutput:
    trades: Mapping[str, object]
    summary: Mapping[str, object]


def run_exit_backtest(
    datasets: Sequence[Mapping[str, object]],
    cross_results: Sequence[CrossScanResult],
    moving_average_periods: Sequence[int] = (5, 20, 60),
    moving_average_method: str = "SMA",
    applied_price: str = "CLOSE",
    profit_targets: Sequence[float] = DEFAULT_PROFIT_TARGETS,
    moving_average_exits_only: bool = False,
    point_size: float = 0.01,
    digits: int = 2,
    cost_mode: str = "none",
    end_bar_shift: int = 1,
    scan_from: Optional[datetime] = None,
    scan_to: Optional[datetime] = None,
    split_ratio: float = 0.7,
    expected_symbol: str = "GOLD",
    input_sha256: Optional[str] = None,
) -> BacktestOutput:
    """M30の買いシグナルに対して全売却戦略を比較する。"""
    active_profit_targets: Sequence[float] = (
        () if moving_average_exits_only else profit_targets
    )
    short_period, middle_period, long_period = _validate_options(
        moving_average_periods=moving_average_periods,
        moving_average_method=moving_average_method,
        applied_price=applied_price,
        profit_targets=active_profit_targets,
        point_size=point_size,
        digits=digits,
        cost_mode=cost_mode,
        end_bar_shift=end_bar_shift,
        scan_from=scan_from,
        scan_to=scan_to,
        split_ratio=split_ratio,
        expected_symbol=expected_symbol,
    )
    prepared = _prepare_datasets(
        datasets=datasets,
        expected_symbol=expected_symbol,
        end_bar_shift=end_bar_shift,
    )
    cross_map = _validate_cross_results(
        cross_results=cross_results,
        expected_symbol=expected_symbol,
    )
    effective_from, effective_to, coverage, warnings = _effective_range(
        prepared=prepared,
        long_period=long_period,
        scan_from=scan_from,
        scan_to=scan_to,
    )
    force_exit = _force_exit_candidate(prepared["M5"], effective_to)
    entry_candidates = _entry_candidates(
        dataset=prepared["M30"],
        crosses=cross_map["M30"].crosses,
        short_period=short_period,
        entry_comparison_period=long_period,
        effective_from=effective_from,
        force_exit_time=force_exit.execution_time,
    )
    death_candidates = _death_candidates(
        prepared=prepared,
        cross_map=cross_map,
        short_period=short_period,
        comparison_periods=(middle_period, long_period),
        effective_from=effective_from,
        force_exit_time=force_exit.execution_time,
    )
    strategies = _build_strategies(
        short_period=short_period,
        comparison_periods=(middle_period, long_period),
        profit_targets=active_profit_targets,
    )
    target_cache = _build_take_profit_cache(
        entries=entry_candidates,
        targets=active_profit_targets,
        m5_dataset=prepared["M5"],
        force_exit=force_exit,
        digits=digits,
        point_size=point_size,
    )

    trades: list[Mapping[str, object]] = []
    for strategy in strategies:
        trades.extend(
            _simulate_strategy(
                strategy=strategy,
                entries=entry_candidates,
                death_candidates=death_candidates,
                target_cache=target_cache,
                force_exit=force_exit,
                m5_dataset=prepared["M5"],
                point_size=point_size,
                digits=digits,
                cost_mode=cost_mode,
            )
        )

    split_at = effective_from + (effective_to - effective_from) * split_ratio
    summaries: Sequence[Mapping[str, object]] = [
        _summarize_strategy(
            strategy=strategy,
            trades=[
                trade
                for trade in trades
                if trade["strategy_id"] == strategy.strategy_id
            ],
            split_at=split_at,
            cost_mode=cost_mode,
            include_compound_metrics=not moving_average_exits_only,
            include_rank_fields=not moving_average_exits_only,
        )
        for strategy in strategies
    ]
    if moving_average_exits_only:
        summaries = _rank_summaries_by_total_profit(
            summaries=summaries,
            cost_mode=cost_mode,
        )
    else:
        summaries = _rank_summaries(summaries=summaries, cost_mode=cost_mode)
    settings: Dict[str, object] = {
        "schema_version": "1.0",
        "input_sha256": input_sha256,
        "symbol": expected_symbol.strip(),
        "moving_average_method": "SMA",
        "applied_price": "CLOSE",
        "periods": [short_period, middle_period, long_period],
        "entry_condition": f"M30_SMA{short_period}_SMA{long_period}_golden_cross",
        "exit_strategy_mode": (
            "moving_average_only" if moving_average_exits_only else "all"
        ),
        "point_size": point_size,
        "digits": digits,
        "cost_mode": cost_mode,
        "spread_assumption": (
            None
            if cost_mode == "none"
            else "chart OHLC is treated as bid; entry ask is open + spread * point_size"
        ),
        "excluded_costs": (
            ["spread", "commission", "slippage", "swap"]
            if cost_mode == "none"
            else ["commission", "slippage", "swap"]
        ),
        "end_bar_shift": end_bar_shift,
        "position_policy": "single_position_per_strategy",
        "additional_entry_policy": "ignore_while_position_is_open",
        "total_profit_unit": "raw GOLD price difference summed across trades",
        "entry_execution": "next_M30_available_bar_open",
        "death_cross_execution": "next_signal_timeframe_available_bar_open",
        "force_close": "last_completed_M5_close_in_common_range",
        "time_basis": "MT5 trade server time; UTC offset is not verified",
        "effective_from": _format_datetime(effective_from),
        "effective_to": _format_datetime(effective_to),
        "split_ratio": split_ratio,
        "split_at": _format_datetime(split_at),
    }
    if moving_average_exits_only:
        settings.update(
            {
                "ranking_basis": (
                    "spread_adjusted" if cost_mode in ("spread", "both") else "gross"
                ),
                "ranking_period": "overall",
                "ranking_method": "total_profit descending, strategy_id ascending",
            }
        )
    else:
        settings.update(
            {
                "profit_targets_pct": [
                    float(target) for target in active_profit_targets
                ],
                "position_sizing": "one unleveraged full-notional position per trade",
                "take_profit_execution": ("M5_open_when_gapped_otherwise_target_price"),
                "profit_target_rounding": "ceil_to_digits",
                "intrabar_assumption": (
                    "a death-cross open execution precedes a take-profit hit later in "
                    "the same M5 bar"
                ),
                "ranking_basis": (
                    "spread_adjusted" if cost_mode in ("spread", "both") else "gross"
                ),
                "ranking_method": (
                    "compounded_return_pct descending, "
                    "maximum_drawdown_pct ascending, trade_count descending"
                ),
            }
        )
    trades_output = {
        "schema_version": "1.0",
        "input_sha256": input_sha256,
        "settings": settings,
        "trade_count": len(trades),
        "trades": trades,
    }
    summary_output = {
        "schema_version": "1.0",
        "input_sha256": input_sha256,
        "settings": settings,
        "coverage": coverage,
        "warnings": warnings,
        "entry_signal_count": len(entry_candidates),
        "strategy_count": len(strategies),
        "trade_count": len(trades),
        "strategies": summaries,
    }
    return BacktestOutput(trades=trades_output, summary=summary_output)


def _validate_options(
    moving_average_periods: Sequence[int],
    moving_average_method: str,
    applied_price: str,
    profit_targets: Sequence[float],
    point_size: float,
    digits: int,
    cost_mode: str,
    end_bar_shift: int,
    scan_from: Optional[datetime],
    scan_to: Optional[datetime],
    split_ratio: float,
    expected_symbol: str,
) -> Tuple[int, int, int]:
    if len(moving_average_periods) != 3:
        raise ValueError("移動平均の期間は短期,中期,長期の3つを指定してください")
    if any(
        isinstance(period, bool) or not isinstance(period, int) or period <= 0
        for period in moving_average_periods
    ):
        raise ValueError("移動平均の期間は1以上の整数で指定してください")
    short_period, middle_period, long_period = moving_average_periods
    if not short_period < middle_period < long_period:
        raise ValueError("移動平均の期間は短期 < 中期 < 長期で指定してください")
    if normalize_moving_average_method(moving_average_method) != "SMA":
        raise ValueError("バックテストの移動平均方式は SMA を指定してください")
    if normalize_applied_price(applied_price) != "CLOSE":
        raise ValueError("バックテストの適用価格は CLOSE を指定してください")
    normalized_targets = []
    for target in profit_targets:
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            raise ValueError("利益率は0より大きい有限の数値で指定してください")
        normalized = float(target)
        if not math.isfinite(normalized) or normalized <= 0:
            raise ValueError("利益率は0より大きい有限の数値で指定してください")
        normalized_targets.append(normalized)
    if len(set(normalized_targets)) != len(normalized_targets):
        raise ValueError("利益率は重複しないように指定してください")
    if (
        isinstance(point_size, bool)
        or not isinstance(point_size, (int, float))
        or not math.isfinite(float(point_size))
        or point_size <= 0
    ):
        raise ValueError("point_size は0より大きい有限の数値で指定してください")
    if isinstance(digits, bool) or not isinstance(digits, int) or digits < 0:
        raise ValueError("digits は0以上の整数で指定してください")
    if cost_mode not in ("none", "spread", "both"):
        raise ValueError("cost_mode は none, spread, both から指定してください")
    if (
        isinstance(end_bar_shift, bool)
        or not isinstance(end_bar_shift, int)
        or end_bar_shift < 0
    ):
        raise ValueError("末尾バーシフトは0以上の整数で指定してください")
    if scan_from is not None and not isinstance(scan_from, datetime):
        raise ValueError("走査開始日時はdatetimeで指定してください")
    if scan_to is not None and not isinstance(scan_to, datetime):
        raise ValueError("走査終了日時はdatetimeで指定してください")
    if scan_from is not None and scan_to is not None and scan_from >= scan_to:
        raise ValueError("走査終了日時は走査開始日時より後に指定してください")
    if (
        isinstance(split_ratio, bool)
        or not isinstance(split_ratio, (int, float))
        or not math.isfinite(float(split_ratio))
        or not 0 < split_ratio < 1
    ):
        raise ValueError("split_ratio は0より大きく1より小さく指定してください")
    if not isinstance(expected_symbol, str) or not expected_symbol.strip():
        raise ValueError("expected_symbol は空でない文字列で指定してください")
    return short_period, middle_period, long_period


def _prepare_datasets(
    datasets: Sequence[Mapping[str, object]],
    expected_symbol: str,
    end_bar_shift: int,
) -> Mapping[str, PreparedDataset]:
    prepared: Dict[str, PreparedDataset] = {}
    for index, dataset in enumerate(datasets):
        symbol = dataset.get("symbol")
        period = dataset.get("period")
        history = dataset.get("history")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(
                f"datasets[{index}].symbol は空でない文字列で指定してください"
            )
        if symbol.strip().upper() != expected_symbol.strip().upper():
            raise ValueError(
                f"datasets[{index}].symbol は {expected_symbol.strip()} で指定してください"
            )
        if (
            not isinstance(period, str)
            or period.strip().upper() not in REQUIRED_PERIODS
        ):
            choices = ", ".join(REQUIRED_PERIODS)
            raise ValueError(
                f"datasets[{index}].period は {choices} から指定してください"
            )
        normalized_period = period.strip().upper()
        if normalized_period in prepared:
            raise ValueError(f"時間足 {normalized_period} が重複しています")
        if not isinstance(history, list) or not history:
            raise ValueError(
                f"datasets[{index}].history は1件以上の配列で指定してください"
            )
        _validate_raw_history_order(history, normalized_period, index)
        bars = TechnicalChart(history=history).history
        if end_bar_shift >= len(bars):
            raise ValueError(
                f"時間足 {normalized_period} は末尾バー除外後の履歴がありません"
            )
        usable_bars = bars[: len(bars) - end_bar_shift] if end_bar_shift else bars
        prepared[normalized_period] = PreparedDataset(
            symbol=symbol.strip(),
            period=normalized_period,
            bars=bars,
            usable_bars=usable_bars,
            times=tuple(bar.time for bar in bars),
            index_by_time={bar.time: bar_index for bar_index, bar in enumerate(bars)},
        )
    missing = [period for period in REQUIRED_PERIODS if period not in prepared]
    if missing:
        raise ValueError(
            f"バックテストに必要な時間足が不足しています: {', '.join(missing)}"
        )
    return prepared


def _validate_raw_history_order(
    history: Sequence[object],
    period: str,
    dataset_index: int,
) -> None:
    previous: Optional[datetime] = None
    interval_seconds = PERIOD_MINUTES[period] * 60
    for bar_index, item in enumerate(history):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"datasets[{dataset_index}].history[{bar_index}] はオブジェクトで指定してください"
            )
        time_text = item.get("time")
        if not isinstance(time_text, str):
            raise ValueError(
                f"datasets[{dataset_index}].history[{bar_index}].time は文字列で指定してください"
            )
        try:
            current = datetime.strptime(time_text, DATETIME_FORMAT)
        except ValueError as exc:
            raise ValueError(
                f"datasets[{dataset_index}].history[{bar_index}].time は"
                " YYYY.MM.DD HH:MM:SS 形式で指定してください"
            ) from exc
        if previous is not None:
            if current == previous:
                raise ValueError(f"時間足 {period} に重複日時があります: {time_text}")
            if current < previous:
                raise ValueError(
                    f"時間足 {period} の日時が昇順ではありません: {time_text}"
                )
            difference = int((current - previous).total_seconds())
            if difference % interval_seconds != 0:
                raise ValueError(
                    f"時間足 {period} に時間間隔の不整合があります: {time_text}"
                )
        previous = current


def _validate_cross_results(
    cross_results: Sequence[CrossScanResult],
    expected_symbol: str,
) -> Mapping[str, CrossScanResult]:
    result = {}
    for cross_result in cross_results:
        period = cross_result.period.strip().upper()
        if period not in REQUIRED_PERIODS:
            continue
        if period in result:
            raise ValueError(f"クロス結果の時間足 {period} が重複しています")
        if cross_result.symbol.strip().upper() != expected_symbol.strip().upper():
            raise ValueError("履歴とクロス結果のシンボルが一致しません")
        result[period] = cross_result
    missing = [period for period in REQUIRED_PERIODS if period not in result]
    if missing:
        raise ValueError(f"クロス結果の時間足が不足しています: {', '.join(missing)}")
    return result


def _effective_range(
    prepared: Mapping[str, PreparedDataset],
    long_period: int,
    scan_from: Optional[datetime],
    scan_to: Optional[datetime],
) -> Tuple[datetime, datetime, Sequence[Mapping[str, object]], Sequence[str]]:
    ready_times = []
    end_times = []
    coverage = []
    warnings = []
    for period in REQUIRED_PERIODS:
        dataset = prepared[period]
        if len(dataset.usable_bars) <= long_period:
            raise ValueError(
                f"時間足 {period} はSMA{long_period}の判定に履歴が不足しています"
            )
        ready_time = dataset.usable_bars[long_period].time
        available_to = dataset.usable_bars[-1].time + timedelta(
            minutes=PERIOD_MINUTES[period]
        )
        ready_times.append(ready_time)
        end_times.append(available_to)
        interval_minutes = PERIOD_MINUTES[period]
        gaps = [
            int((current.time - previous.time).total_seconds() / 60)
            for previous, current in zip(dataset.bars, dataset.bars[1:])
            if current.time - previous.time > timedelta(minutes=interval_minutes)
        ]
        coverage.append(
            {
                "period": period,
                "bar_count": len(dataset.bars),
                "usable_bar_count": len(dataset.usable_bars),
                "first_bar_time": _format_datetime(dataset.bars[0].time),
                "last_bar_time": _format_datetime(dataset.bars[-1].time),
                "warmup_ready_time": _format_datetime(ready_time),
                "available_to": _format_datetime(available_to),
                "interval_gap_count": len(gaps),
                "maximum_interval_gap_minutes": max(gaps) if gaps else 0,
            }
        )
    effective_from = max(ready_times)
    if scan_from is not None:
        late_periods = [
            period
            for period in REQUIRED_PERIODS
            if prepared[period].bars[0].time > scan_from
        ]
        if late_periods:
            warnings.append(
                f"{', '.join(late_periods)}データは指定開始日時より後から始まるため、"
                "共通期間を短縮しました"
            )
        effective_from = max(effective_from, scan_from)
    effective_to = min(end_times)
    if scan_to is not None:
        if effective_to < scan_to:
            warnings.append(
                "入力データの終了日時が指定終了日時より前のため、共通期間を短縮しました"
            )
        effective_to = min(effective_to, scan_to)
    if effective_from >= effective_to:
        raise ValueError("バックテスト可能な共通期間がありません")
    return effective_from, effective_to, coverage, warnings


def _force_exit_candidate(
    m5_dataset: PreparedDataset,
    effective_to: datetime,
) -> ExitCandidate:
    close_delta = timedelta(minutes=PERIOD_MINUTES["M5"])
    target_start = effective_to - close_delta
    index = bisect_right(m5_dataset.times, target_start) - 1
    index = min(index, len(m5_dataset.usable_bars) - 1)
    if index < 0:
        raise ValueError("強制決済に使用できるM5確定足がありません")
    bar = m5_dataset.usable_bars[index]
    execution_time = bar.time + close_delta
    if execution_time > effective_to:
        raise ValueError("強制決済時刻が共通検証期間を超えています")
    return ExitCandidate(
        signal_bar_time=None,
        signal_available_time=None,
        execution_time=execution_time,
        execution_price=bar.close,
        reason="force_close",
        bar_time=bar.time,
        forced=True,
    )


def _entry_candidates(
    dataset: PreparedDataset,
    crosses: Sequence[MovingAverageCross],
    short_period: int,
    entry_comparison_period: int,
    effective_from: datetime,
    force_exit_time: datetime,
) -> Sequence[EntryCandidate]:
    entries = []
    usable_last_index = len(dataset.usable_bars) - 1
    for cross in crosses:
        if not (
            cross.cross_type == "golden_cross"
            and cross.short_period == short_period
            and cross.comparison_period == entry_comparison_period
        ):
            continue
        bar_index = dataset.index_by_time.get(cross.bar_time)
        if bar_index is None or bar_index + 1 > usable_last_index:
            continue
        execution_bar = dataset.bars[bar_index + 1]
        if execution_bar.time < effective_from or execution_bar.time >= force_exit_time:
            continue
        entries.append(
            EntryCandidate(
                signal_bar_time=cross.bar_time,
                signal_available_time=cross.bar_time
                + timedelta(minutes=PERIOD_MINUTES["M30"]),
                execution_time=execution_bar.time,
                execution_price=execution_bar.open,
                spread_points=execution_bar.spread,
            )
        )
    entries.sort(key=lambda entry: entry.execution_time)
    return entries


def _death_candidates(
    prepared: Mapping[str, PreparedDataset],
    cross_map: Mapping[str, CrossScanResult],
    short_period: int,
    comparison_periods: Sequence[int],
    effective_from: datetime,
    force_exit_time: datetime,
) -> Mapping[Tuple[str, int], Sequence[ExitCandidate]]:
    result = {}
    for period in REQUIRED_PERIODS:
        dataset = prepared[period]
        usable_last_index = len(dataset.usable_bars) - 1
        for comparison_period in comparison_periods:
            candidates = []
            for cross in cross_map[period].crosses:
                if not (
                    cross.cross_type == "death_cross"
                    and cross.short_period == short_period
                    and cross.comparison_period == comparison_period
                ):
                    continue
                bar_index = dataset.index_by_time.get(cross.bar_time)
                if bar_index is None or bar_index + 1 > usable_last_index:
                    continue
                execution_bar = dataset.bars[bar_index + 1]
                if (
                    execution_bar.time < effective_from
                    or execution_bar.time > force_exit_time
                ):
                    continue
                candidates.append(
                    ExitCandidate(
                        signal_bar_time=cross.bar_time,
                        signal_available_time=cross.bar_time
                        + timedelta(minutes=PERIOD_MINUTES[period]),
                        execution_time=execution_bar.time,
                        execution_price=execution_bar.open,
                        reason="death_cross",
                        bar_time=execution_bar.time,
                    )
                )
            candidates.sort(key=lambda candidate: candidate.execution_time)
            result[(period, comparison_period)] = tuple(candidates)
    return result


def _build_strategies(
    short_period: int,
    comparison_periods: Sequence[int],
    profit_targets: Sequence[float],
) -> Sequence[ExitStrategy]:
    strategies = []
    for period in REQUIRED_PERIODS:
        for comparison_period in comparison_periods:
            strategies.append(
                ExitStrategy(
                    strategy_id=(f"death_{period}_{short_period}_{comparison_period}"),
                    death_period=period,
                    comparison_period=comparison_period,
                    profit_target_pct=None,
                )
            )
    for target in profit_targets:
        strategies.append(
            ExitStrategy(
                strategy_id=f"take_profit_{_target_id(target)}",
                death_period=None,
                comparison_period=None,
                profit_target_pct=float(target),
            )
        )
    for period in REQUIRED_PERIODS:
        for comparison_period in comparison_periods:
            for target in profit_targets:
                strategies.append(
                    ExitStrategy(
                        strategy_id=(
                            f"death_{period}_{short_period}_{comparison_period}"
                            f"_or_take_profit_{_target_id(target)}"
                        ),
                        death_period=period,
                        comparison_period=comparison_period,
                        profit_target_pct=float(target),
                    )
                )
    strategy_ids = [strategy.strategy_id for strategy in strategies]
    if len(strategy_ids) != len(set(strategy_ids)):
        raise ValueError("売却戦略IDが重複しています")
    return tuple(strategies)


def _build_take_profit_cache(
    entries: Sequence[EntryCandidate],
    targets: Sequence[float],
    m5_dataset: PreparedDataset,
    force_exit: ExitCandidate,
    digits: int,
    point_size: float,
) -> Mapping[Tuple[datetime, float], Optional[ExitCandidate]]:
    cache = {}
    for entry in entries:
        for target in targets:
            cache[(entry.execution_time, float(target))] = _find_take_profit(
                entry=entry,
                target_pct=float(target),
                m5_dataset=m5_dataset,
                force_exit=force_exit,
                digits=digits,
                point_size=point_size,
            )
    return cache


def _find_take_profit(
    entry: EntryCandidate,
    target_pct: float,
    m5_dataset: PreparedDataset,
    force_exit: ExitCandidate,
    digits: int,
    point_size: float,
) -> Optional[ExitCandidate]:
    target_price = _ceil_price(
        entry.execution_price * (1 + target_pct / 100),
        digits,
    )
    tolerance = point_size / 2
    start_index = bisect_left(m5_dataset.times, entry.execution_time)
    for bar in m5_dataset.usable_bars[start_index:]:
        if bar.time >= force_exit.execution_time:
            break
        if bar.open + tolerance >= target_price:
            return ExitCandidate(
                signal_bar_time=bar.time,
                signal_available_time=bar.time,
                execution_time=bar.time,
                execution_price=bar.open,
                reason="take_profit_gap",
                bar_time=bar.time,
            )
        if bar.high + tolerance >= target_price:
            execution_time = min(
                bar.time + timedelta(minutes=PERIOD_MINUTES["M5"]),
                force_exit.execution_time,
            )
            return ExitCandidate(
                signal_bar_time=bar.time,
                signal_available_time=execution_time,
                execution_time=execution_time,
                execution_price=target_price,
                reason="take_profit",
                bar_time=bar.time,
            )
    return None


def _simulate_strategy(
    strategy: ExitStrategy,
    entries: Sequence[EntryCandidate],
    death_candidates: Mapping[Tuple[str, int], Sequence[ExitCandidate]],
    target_cache: Mapping[Tuple[datetime, float], Optional[ExitCandidate]],
    force_exit: ExitCandidate,
    m5_dataset: PreparedDataset,
    point_size: float,
    digits: int,
    cost_mode: str,
) -> Sequence[Mapping[str, object]]:
    strategy_trades: list[Mapping[str, object]] = []
    position_closed_at: Optional[datetime] = None
    death_list: Sequence[ExitCandidate] = ()
    death_times: Sequence[datetime] = ()
    if strategy.death_period is not None and strategy.comparison_period is not None:
        death_list = death_candidates[
            (strategy.death_period, strategy.comparison_period)
        ]
        death_times = tuple(candidate.execution_time for candidate in death_list)
    for entry in entries:
        if (
            position_closed_at is not None
            and entry.execution_time <= position_closed_at
        ):
            continue
        death_exit = None
        if death_list:
            death_index = bisect_right(death_times, entry.execution_time)
            if death_index < len(death_list):
                candidate = death_list[death_index]
                if candidate.execution_time <= force_exit.execution_time:
                    death_exit = candidate
        target_exit = None
        if strategy.profit_target_pct is not None:
            target_exit = target_cache[
                (entry.execution_time, strategy.profit_target_pct)
            ]
        exit_candidate = _select_exit(
            death_exit=death_exit,
            target_exit=target_exit,
            force_exit=force_exit,
        )
        trade = _build_trade(
            strategy=strategy,
            sequence=len(strategy_trades) + 1,
            entry=entry,
            exit_candidate=exit_candidate,
            m5_dataset=m5_dataset,
            point_size=point_size,
            digits=digits,
            cost_mode=cost_mode,
        )
        strategy_trades.append(trade)
        position_closed_at = exit_candidate.execution_time
    return strategy_trades


def _select_exit(
    death_exit: Optional[ExitCandidate],
    target_exit: Optional[ExitCandidate],
    force_exit: ExitCandidate,
) -> ExitCandidate:
    candidates = [candidate for candidate in (death_exit, target_exit) if candidate]
    if not candidates:
        return force_exit
    chosen = min(candidates, key=lambda candidate: candidate.execution_time)
    if death_exit is not None and target_exit is not None:
        if death_exit.execution_time == target_exit.execution_time:
            if target_exit.reason == "take_profit":
                chosen = target_exit
            else:
                chosen = death_exit
    if chosen.execution_time > force_exit.execution_time:
        return force_exit
    return chosen


def _build_trade(
    strategy: ExitStrategy,
    sequence: int,
    entry: EntryCandidate,
    exit_candidate: ExitCandidate,
    m5_dataset: PreparedDataset,
    point_size: float,
    digits: int,
    cost_mode: str,
) -> Mapping[str, object]:
    entry_with_spread = entry.execution_price + entry.spread_points * point_size
    gross_profit = exit_candidate.execution_price - entry.execution_price
    spread_profit = exit_candidate.execution_price - entry_with_spread
    gross_return = gross_profit / entry.execution_price * 100
    spread_return = spread_profit / entry_with_spread * 100
    maximum, minimum = _holding_extrema(
        entry=entry,
        exit_candidate=exit_candidate,
        m5_dataset=m5_dataset,
    )
    mfe_pct = (maximum - entry.execution_price) / entry.execution_price * 100
    mae_pct = (minimum - entry.execution_price) / entry.execution_price * 100
    holding_minutes = int(
        (exit_candidate.execution_time - entry.execution_time).total_seconds() / 60
    )
    trade: Dict[str, object] = {
        "trade_id": f"{strategy.strategy_id}_{sequence:04d}",
        "strategy_id": strategy.strategy_id,
        "sequence": sequence,
        "death_period": strategy.death_period,
        "comparison_period": strategy.comparison_period,
        "profit_target_pct": strategy.profit_target_pct,
        "entry_signal_bar_time": _format_datetime(entry.signal_bar_time),
        "entry_signal_available_time": _format_datetime(entry.signal_available_time),
        "entry_time": _format_datetime(entry.execution_time),
        "entry_price": _round_price(entry.execution_price, digits),
        "exit_signal_bar_time": (
            _format_datetime(exit_candidate.signal_bar_time)
            if exit_candidate.signal_bar_time
            else None
        ),
        "exit_signal_available_time": (
            _format_datetime(exit_candidate.signal_available_time)
            if exit_candidate.signal_available_time
            else None
        ),
        "exit_time": _format_datetime(exit_candidate.execution_time),
        "exit_price": _round_price(exit_candidate.execution_price, digits),
        "exit_reason": exit_candidate.reason,
        "forced": exit_candidate.forced,
        "holding_minutes": holding_minutes,
        "gross_profit": _round_metric(gross_profit),
        "gross_return_pct": _round_metric(gross_return),
        "maximum_price_while_held": _round_price(maximum, digits),
        "minimum_price_while_held": _round_price(minimum, digits),
        "maximum_favorable_excursion_pct": _round_metric(mfe_pct),
        "maximum_adverse_excursion_pct": _round_metric(mae_pct),
    }
    if cost_mode in ("spread", "both"):
        trade.update(
            {
                "entry_spread_points": entry.spread_points,
                "entry_price_with_spread": _round_price(entry_with_spread, digits),
                "spread_adjusted_profit": _round_metric(spread_profit),
                "spread_adjusted_return_pct": _round_metric(spread_return),
            }
        )
    return trade


def _holding_extrema(
    entry: EntryCandidate,
    exit_candidate: ExitCandidate,
    m5_dataset: PreparedDataset,
) -> Tuple[float, float]:
    maximum = max(entry.execution_price, exit_candidate.execution_price)
    minimum = min(entry.execution_price, exit_candidate.execution_price)
    start_index = bisect_left(m5_dataset.times, entry.execution_time)
    for bar in m5_dataset.usable_bars[start_index:]:
        if bar.time >= exit_candidate.execution_time:
            break
        if (
            exit_candidate.reason == "take_profit"
            and bar.time == exit_candidate.bar_time
        ):
            break
        maximum = max(maximum, bar.high)
        minimum = min(minimum, bar.low)
    return maximum, minimum


def _summarize_strategy(
    strategy: ExitStrategy,
    trades: Sequence[Mapping[str, object]],
    split_at: datetime,
    cost_mode: str,
    include_compound_metrics: bool,
    include_rank_fields: bool,
) -> Mapping[str, object]:
    first = [trade for trade in trades if _trade_entry_time(trade) < split_at]
    second = [trade for trade in trades if _trade_entry_time(trade) >= split_at]
    periods: Dict[str, object] = {}
    for name, selected in (("overall", trades), ("first", first), ("second", second)):
        period_metrics: Dict[str, object] = {
            "trade_count": len(selected),
            "forced_close_count": sum(bool(trade["forced"]) for trade in selected),
            "average_holding_minutes": _average_optional(
                [float(cast(int, trade["holding_minutes"])) for trade in selected]
            ),
            "maximum_holding_minutes": (
                max(cast(int, trade["holding_minutes"]) for trade in selected)
                if selected
                else 0
            ),
            "average_mfe_pct": _average_optional(
                [
                    cast(float, trade["maximum_favorable_excursion_pct"])
                    for trade in selected
                ]
            ),
            "average_mae_pct": _average_optional(
                [
                    cast(float, trade["maximum_adverse_excursion_pct"])
                    for trade in selected
                ]
            ),
        }
        if cost_mode in ("none", "both"):
            period_metrics["gross"] = _performance_metrics(
                trades=selected,
                profit_key="gross_profit",
                return_key="gross_return_pct",
                include_compound_metrics=include_compound_metrics,
            )
        if cost_mode in ("spread", "both"):
            period_metrics["spread_adjusted"] = _performance_metrics(
                trades=selected,
                profit_key="spread_adjusted_profit",
                return_key="spread_adjusted_return_pct",
                include_compound_metrics=include_compound_metrics,
            )
        periods[name] = period_metrics
    summary = {
        "strategy_id": strategy.strategy_id,
        "death_period": strategy.death_period,
        "comparison_period": strategy.comparison_period,
        "profit_target_pct": strategy.profit_target_pct,
        "periods": periods,
    }
    if include_rank_fields:
        summary.update(
            {
                "rank": None,
                "rank_overall": None,
                "rank_first": None,
                "rank_second": None,
            }
        )
    return summary


def _performance_metrics(
    trades: Sequence[Mapping[str, object]],
    profit_key: str,
    return_key: str,
    include_compound_metrics: bool,
) -> Mapping[str, object]:
    profits = [cast(float, trade[profit_key]) for trade in trades]
    returns = [cast(float, trade[return_key]) for trade in trades]
    wins = [profit for profit in profits if profit > 0]
    losses = [profit for profit in profits if profit < 0]
    profit_factor = None
    if losses:
        profit_factor = sum(wins) / abs(sum(losses))
    metrics = {
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": len(profits) - len(wins) - len(losses),
        "win_rate_pct": _round_metric(len(wins) / len(trades) * 100) if trades else 0.0,
        "total_profit": _round_metric(sum(profits)),
        "sum_return_pct": _round_metric(sum(returns)),
        "average_return_pct": _round_metric(mean(returns)) if returns else None,
        "median_return_pct": _round_metric(median(returns)) if returns else None,
        "maximum_return_pct": _round_metric(max(returns)) if returns else None,
        "minimum_return_pct": _round_metric(min(returns)) if returns else None,
        "profit_factor": (
            _round_metric(profit_factor) if profit_factor is not None else None
        ),
    }
    if include_compound_metrics:
        equity = 1.0
        peak = 1.0
        maximum_drawdown = 0.0
        for return_pct in returns:
            equity *= 1 + return_pct / 100
            peak = max(peak, equity)
            if peak > 0:
                maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak * 100)
        metrics.update(
            {
                "compounded_return_pct": _round_metric((equity - 1) * 100),
                "maximum_drawdown_pct": _round_metric(maximum_drawdown),
            }
        )
    return metrics


def _rank_summaries(
    summaries: Sequence[Mapping[str, object]],
    cost_mode: str,
) -> Sequence[Mapping[str, object]]:
    metric_name = "spread_adjusted" if cost_mode in ("spread", "both") else "gross"

    def key(
        summary: Mapping[str, object],
        period_name: str,
    ) -> Tuple[float, float, int, str]:
        periods = summary["periods"]
        assert isinstance(periods, Mapping)
        period = periods[period_name]
        assert isinstance(period, Mapping)
        metrics = period[metric_name]
        assert isinstance(metrics, Mapping)
        return (
            -float(metrics["compounded_return_pct"]),
            float(metrics["maximum_drawdown_pct"]),
            -int(period["trade_count"]),
            str(summary["strategy_id"]),
        )

    ranks: Dict[str, Dict[str, int]] = {}
    for period_name in ("overall", "first", "second"):
        period_ranked = sorted(
            summaries,
            key=lambda summary: key(summary, period_name),
        )
        for rank, summary in enumerate(period_ranked, start=1):
            strategy_id = str(summary["strategy_id"])
            ranks.setdefault(strategy_id, {})[period_name] = rank

    ranked = sorted(summaries, key=lambda summary: key(summary, "overall"))
    result = []
    for summary in ranked:
        item = dict(summary)
        strategy_ranks = ranks[str(summary["strategy_id"])]
        item["rank"] = strategy_ranks["overall"]
        item["rank_overall"] = strategy_ranks["overall"]
        item["rank_first"] = strategy_ranks["first"]
        item["rank_second"] = strategy_ranks["second"]
        result.append(item)
    return result


def _rank_summaries_by_total_profit(
    summaries: Sequence[Mapping[str, object]],
    cost_mode: str,
) -> Sequence[Mapping[str, object]]:
    metric_name = "spread_adjusted" if cost_mode in ("spread", "both") else "gross"

    def key(summary: Mapping[str, object]) -> Tuple[float, str]:
        periods = summary["periods"]
        assert isinstance(periods, Mapping)
        overall = periods["overall"]
        assert isinstance(overall, Mapping)
        metrics = overall[metric_name]
        assert isinstance(metrics, Mapping)
        return -float(metrics["total_profit"]), str(summary["strategy_id"])

    result = []
    for rank, summary in enumerate(sorted(summaries, key=key), start=1):
        item = dict(summary)
        item["rank"] = rank
        item["rank_overall"] = rank
        result.append(item)
    return result


def _trade_entry_time(trade: Mapping[str, object]) -> datetime:
    return datetime.strptime(str(trade["entry_time"]), DATETIME_FORMAT)


def _average_optional(values: Sequence[float]) -> Optional[float]:
    return _round_metric(mean(values)) if values else None


def _target_id(target: float) -> str:
    return f"{float(target):.2f}".replace(".", "_")


def _round_price(value: float, digits: int) -> float:
    quantum = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _ceil_price(value: float, digits: int) -> float:
    quantum = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_CEILING))


def _round_metric(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("集計結果に有限でない数値が含まれています")
    return round(float(value), 8)


def _format_datetime(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)
