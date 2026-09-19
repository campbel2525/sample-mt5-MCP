from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations
from statistics import mean
from typing import Dict, Literal, Mapping, Optional, Sequence, Tuple, cast

from services.chart_service import MarketBar, TechnicalChart

DATETIME_FORMAT = "%Y.%m.%d %H:%M:%S"
PERIODS = ("M5", "M15", "M30", "H1")
PERIOD_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60}
Direction = Literal["long", "short"]


@dataclass(frozen=True)
class PeriodSeries:
    period: str
    bars: Tuple[MarketBar, ...]
    usable_bars: Tuple[MarketBar, ...]
    times: Tuple[datetime, ...]
    moving_averages: Mapping[int, Tuple[Optional[float], ...]]


@dataclass(frozen=True)
class ResearchPanel:
    symbol: str
    moving_average_periods: Tuple[int, int, int]
    times: Tuple[datetime, ...]
    bars: Tuple[MarketBar, ...]
    effective_from: datetime
    effective_to: datetime
    series: Mapping[str, PeriodSeries]
    bullish_order: Mapping[str, Tuple[bool, ...]]
    bearish_order: Mapping[str, Tuple[bool, ...]]
    trend_up: Mapping[str, Tuple[bool, ...]]
    trend_down: Mapping[str, Tuple[bool, ...]]
    crosses: Mapping[Tuple[str, int, str], Tuple[int, ...]]


@dataclass(frozen=True)
class EntryRule:
    rule_id: str
    label: str
    direction: Direction
    initial_indices: Tuple[int, ...]
    reentry_indices: Tuple[int, ...]


@dataclass(frozen=True)
class ExitRule:
    rule_id: str
    label: str
    direction: Direction
    indices: Tuple[int, ...]


@dataclass(frozen=True)
class ResearchStrategy:
    strategy_id: str
    direction: Direction
    entry_rule: EntryRule
    exit_rule: ExitRule


@dataclass(frozen=True)
class SimulationResult:
    metrics: Mapping[str, object]
    trades: Sequence[Mapping[str, object]]


@dataclass(frozen=True)
class StrategySimulationSpec:
    panel: ResearchPanel
    strategy: ResearchStrategy
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_stop: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    maximum_holding_minutes: Optional[int] = None


def prepare_research_panel(
    datasets: Sequence[Mapping[str, object]],
    moving_average_periods: Sequence[int] = (5, 20, 60),
    scan_from: Optional[datetime] = None,
    scan_to: Optional[datetime] = None,
    end_bar_shift: int = 1,
    expected_symbol: str = "GOLD",
) -> ResearchPanel:
    short_period, middle_period, long_period = _validate_options(
        moving_average_periods=moving_average_periods,
        scan_from=scan_from,
        scan_to=scan_to,
        end_bar_shift=end_bar_shift,
        expected_symbol=expected_symbol,
    )
    prepared: Dict[str, PeriodSeries] = {}
    for index, dataset in enumerate(datasets):
        symbol = dataset.get("symbol")
        period = dataset.get("period")
        history = dataset.get("history")
        if (
            not isinstance(symbol, str)
            or symbol.strip().upper() != expected_symbol.upper()
        ):
            raise ValueError(
                f"datasets[{index}].symbol は {expected_symbol} で指定してください"
            )
        if not isinstance(period, str) or period.strip().upper() not in PERIODS:
            raise ValueError(
                f"datasets[{index}].period は {', '.join(PERIODS)} から指定してください"
            )
        normalized_period = period.strip().upper()
        if normalized_period in prepared:
            raise ValueError(f"時間足 {normalized_period} が重複しています")
        if not isinstance(history, list) or not history:
            raise ValueError(
                f"datasets[{index}].history は1件以上の配列で指定してください"
            )
        chart = TechnicalChart(history=history)
        if end_bar_shift >= len(chart.history):
            raise ValueError(
                f"時間足 {normalized_period} は末尾バー除外後の履歴がありません"
            )
        usable_bars = (
            chart.history[: len(chart.history) - end_bar_shift]
            if end_bar_shift
            else chart.history
        )
        prepared[normalized_period] = PeriodSeries(
            period=normalized_period,
            bars=chart.history,
            usable_bars=usable_bars,
            times=tuple(bar.time for bar in chart.history),
            moving_averages={
                period_value: chart.moving_average(period_value)
                for period_value in (short_period, middle_period, long_period)
            },
        )
    missing = [period for period in PERIODS if period not in prepared]
    if missing:
        raise ValueError(f"検証に必要な時間足が不足しています: {', '.join(missing)}")

    ready_times = [
        prepared[period].usable_bars[long_period - 1].time
        + timedelta(minutes=PERIOD_MINUTES[period])
        for period in PERIODS
    ]
    available_to = [
        prepared[period].usable_bars[-1].time
        + timedelta(minutes=PERIOD_MINUTES[period])
        for period in PERIODS
    ]
    effective_from = max(ready_times)
    effective_to = min(available_to)
    if scan_from is not None:
        effective_from = max(effective_from, scan_from)
    if scan_to is not None:
        effective_to = min(effective_to, scan_to)
    if effective_from >= effective_to:
        raise ValueError("検証可能な共通期間がありません")

    m5_series = prepared["M5"]
    grid_bars = tuple(
        bar
        for bar in m5_series.usable_bars
        if effective_from <= bar.time < effective_to
    )
    if not grid_bars:
        raise ValueError("検証可能なM5バーがありません")
    grid_times = tuple(bar.time for bar in grid_bars)
    completed_indices = {
        period: _completed_indices(
            series=prepared[period],
            grid_times=grid_times,
        )
        for period in PERIODS
    }

    bullish_order = {}
    bearish_order = {}
    trend_up = {}
    trend_down = {}
    for period in PERIODS:
        series = prepared[period]
        indices = completed_indices[period]
        short_values = series.moving_averages[short_period]
        middle_values = series.moving_averages[middle_period]
        long_values = series.moving_averages[long_period]
        bullish_order[period] = tuple(
            _ordered_state(
                index=item,
                short_values=short_values,
                middle_values=middle_values,
                long_values=long_values,
                direction="long",
            )
            for item in indices
        )
        bearish_order[period] = tuple(
            _ordered_state(
                index=item,
                short_values=short_values,
                middle_values=middle_values,
                long_values=long_values,
                direction="short",
            )
            for item in indices
        )
        trend_up[period] = tuple(
            _relation_state(item, short_values, long_values, "long") for item in indices
        )
        trend_down[period] = tuple(
            _relation_state(item, short_values, long_values, "short")
            for item in indices
        )

    crosses: Dict[Tuple[str, int, str], Tuple[int, ...]] = {}
    for period in PERIODS:
        for comparison_period in (middle_period, long_period):
            for cross_type in ("golden_cross", "death_cross"):
                crosses[(period, comparison_period, cross_type)] = tuple(
                    _cross_indices(
                        series=prepared[period],
                        completed_indices=completed_indices[period],
                        short_period=short_period,
                        comparison_period=comparison_period,
                        cross_type=cross_type,
                    )
                )

    return ResearchPanel(
        symbol=expected_symbol,
        moving_average_periods=(short_period, middle_period, long_period),
        times=grid_times,
        bars=grid_bars,
        effective_from=effective_from,
        effective_to=effective_to,
        series=prepared,
        bullish_order=bullish_order,
        bearish_order=bearish_order,
        trend_up=trend_up,
        trend_down=trend_down,
        crosses=crosses,
    )


def build_entry_rules(
    panel: ResearchPanel,
    direction: Direction,
) -> Sequence[EntryRule]:
    _validate_direction(direction)
    short_period, middle_period, long_period = panel.moving_average_periods
    order_states = panel.bullish_order if direction == "long" else panel.bearish_order
    trend_states = panel.trend_up if direction == "long" else panel.trend_down
    cross_type = "golden_cross" if direction == "long" else "death_cross"
    rules = []

    for size in range(1, len(PERIODS) + 1):
        for subset in combinations(PERIODS, size):
            state = tuple(
                all(order_states[period][index] for period in subset)
                for index in range(len(panel.times))
            )
            transition = _transition_indices(state, False, True)
            state_indices = _true_indices(state)
            subset_id = "_".join(subset)
            label = f"{', '.join(subset)}で短期・中期・長期の順が完成"
            rules.append(
                EntryRule(
                    rule_id=f"po_complete_{subset_id}",
                    label=label,
                    direction=direction,
                    initial_indices=transition,
                    reentry_indices=transition,
                )
            )
            rules.append(
                EntryRule(
                    rule_id=f"po_reentry_{subset_id}",
                    label=f"{label}、決済後は並びが成立すれば再エントリー",
                    direction=direction,
                    initial_indices=transition,
                    reentry_indices=state_indices,
                )
            )

    order_count = tuple(
        sum(order_states[period][index] for period in PERIODS)
        for index in range(len(panel.times))
    )
    for threshold in range(1, len(PERIODS) + 1):
        state = tuple(count >= threshold for count in order_count)
        transition = _transition_indices(state, False, True)
        state_indices = _true_indices(state)
        label = f"4時間足中{threshold}個以上で短期・中期・長期の順が完成"
        rules.append(
            EntryRule(
                rule_id=f"po_count_{threshold}_complete",
                label=label,
                direction=direction,
                initial_indices=transition,
                reentry_indices=transition,
            )
        )
        rules.append(
            EntryRule(
                rule_id=f"po_count_{threshold}_reentry",
                label=f"{label}、決済後は条件成立中に再エントリー",
                direction=direction,
                initial_indices=transition,
                reentry_indices=state_indices,
            )
        )

    all_order_state = tuple(
        all(order_states[period][index] for period in PERIODS)
        for index in range(len(panel.times))
    )
    all_trend_state = tuple(
        all(trend_states[period][index] for period in PERIODS)
        for index in range(len(panel.times))
    )
    trend_label = (
        "4時間足すべてで短期が長期より上"
        if direction == "long"
        else "4時間足すべてで短期が長期より下"
    )
    hierarchy = {"M5": PERIODS, "M15": PERIODS[1:], "M30": PERIODS[2:], "H1": ("H1",)}
    for signal_period in PERIODS:
        higher_order_state = tuple(
            all(order_states[period][index] for period in hierarchy[signal_period])
            for index in range(len(panel.times))
        )
        for comparison_period in (middle_period, long_period):
            base_indices = panel.crosses[(signal_period, comparison_period, cross_type)]
            filters = (
                ("none", "確認条件なし", None),
                ("all_po", "4時間足すべてが短期・中期・長期の順", all_order_state),
                ("all_trend", trend_label, all_trend_state),
                (
                    "higher_po",
                    f"{signal_period}以上の時間足が短期・中期・長期の順",
                    higher_order_state,
                ),
            )
            for filter_id, filter_label, filter_state in filters:
                indices = (
                    base_indices
                    if filter_state is None
                    else tuple(index for index in base_indices if filter_state[index])
                )
                direction_label = (
                    "ゴールデンクロス" if direction == "long" else "デッドクロス"
                )
                rules.append(
                    EntryRule(
                        rule_id=(
                            f"cross_{signal_period}_{short_period}_"
                            f"{comparison_period}_{filter_id}"
                        ),
                        label=(
                            f"{signal_period}のSMA{short_period}・"
                            f"SMA{comparison_period}{direction_label}、{filter_label}"
                        ),
                        direction=direction,
                        initial_indices=indices,
                        reentry_indices=indices,
                    )
                )

    if direction == "long":
        rules.extend(
            (
                _filtered_cross_entry_rule(
                    panel=panel,
                    direction=direction,
                    signal_period="M30",
                    comparison_period=long_period,
                    confirmation_periods=("M5", "M15"),
                    rule_id="validation1_M30",
                    label="検証1のM30買い条件",
                ),
                _filtered_cross_entry_rule(
                    panel=panel,
                    direction=direction,
                    signal_period="H1",
                    comparison_period=long_period,
                    confirmation_periods=("M5", "M15", "M30"),
                    rule_id="validation1_H1",
                    label="検証1のH1買い条件",
                ),
            )
        )
    else:
        rules.extend(
            (
                _filtered_cross_entry_rule(
                    panel=panel,
                    direction=direction,
                    signal_period="M30",
                    comparison_period=long_period,
                    confirmation_periods=("M5", "M15"),
                    rule_id="reverse_validation1_M30",
                    label="検証1を反転したM30売り条件",
                ),
                _filtered_cross_entry_rule(
                    panel=panel,
                    direction=direction,
                    signal_period="H1",
                    comparison_period=long_period,
                    confirmation_periods=("M5", "M15", "M30"),
                    rule_id="reverse_validation1_H1",
                    label="検証1を反転したH1売り条件",
                ),
            )
        )
    return tuple(rules)


def build_exit_rules(
    panel: ResearchPanel,
    direction: Direction,
) -> Sequence[ExitRule]:
    _validate_direction(direction)
    short_period, middle_period, long_period = panel.moving_average_periods
    order_states = panel.bullish_order if direction == "long" else panel.bearish_order
    reverse_trends = panel.trend_down if direction == "long" else panel.trend_up
    cross_type = "death_cross" if direction == "long" else "golden_cross"
    rules = []

    for period in PERIODS:
        for comparison_period in (middle_period, long_period):
            direction_label = (
                "デッドクロス" if direction == "long" else "ゴールデンクロス"
            )
            rules.append(
                ExitRule(
                    rule_id=f"cross_{period}_{short_period}_{comparison_period}",
                    label=(
                        f"{period}のSMA{short_period}・SMA{comparison_period}"
                        f"{direction_label}"
                    ),
                    direction=direction,
                    indices=panel.crosses[(period, comparison_period, cross_type)],
                )
            )

    for size in range(1, len(PERIODS) + 1):
        for subset in combinations(PERIODS, size):
            state = tuple(
                all(order_states[period][index] for period in subset)
                for index in range(len(panel.times))
            )
            subset_id = "_".join(subset)
            rules.append(
                ExitRule(
                    rule_id=f"po_break_{subset_id}",
                    label=f"{', '.join(subset)}の短期・中期・長期の順が崩れる",
                    direction=direction,
                    indices=_transition_indices(state, True, False),
                )
            )

    order_count = tuple(
        sum(order_states[period][index] for period in PERIODS)
        for index in range(len(panel.times))
    )
    reverse_count = tuple(
        sum(reverse_trends[period][index] for period in PERIODS)
        for index in range(len(panel.times))
    )
    for threshold in range(1, len(PERIODS) + 1):
        order_state = tuple(count >= threshold for count in order_count)
        reverse_state = tuple(count >= threshold for count in reverse_count)
        rules.append(
            ExitRule(
                rule_id=f"po_count_{threshold}_break",
                label=f"パーフェクトオーダー成立が{threshold}時間足未満になる",
                direction=direction,
                indices=_transition_indices(order_state, True, False),
            )
        )
        rules.append(
            ExitRule(
                rule_id=f"reverse_trend_count_{threshold}",
                label=(
                    f"反対方向のSMA{short_period}・SMA{long_period}が"
                    f"{threshold}時間足以上で成立"
                ),
                direction=direction,
                indices=_transition_indices(reverse_state, False, True),
            )
        )
    return tuple(rules)


def build_strategies(
    entry_rules: Sequence[EntryRule],
    exit_rules: Sequence[ExitRule],
) -> Sequence[ResearchStrategy]:
    strategies = []
    for entry_rule in entry_rules:
        for exit_rule in exit_rules:
            if entry_rule.direction != exit_rule.direction:
                continue
            strategies.append(
                ResearchStrategy(
                    strategy_id=(
                        f"{entry_rule.direction}__entry_{entry_rule.rule_id}__"
                        f"exit_{exit_rule.rule_id}"
                    ),
                    direction=entry_rule.direction,
                    entry_rule=entry_rule,
                    exit_rule=exit_rule,
                )
            )
    return tuple(strategies)


def simulate_strategy(
    panel: ResearchPanel,
    strategy: ResearchStrategy,
    scan_from: datetime,
    scan_to: datetime,
    point_size: float = 0.01,
    collect_trades: bool = False,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    trailing_stop: Optional[float] = None,
    trailing_stop_pct: Optional[float] = None,
    maximum_holding_minutes: Optional[int] = None,
) -> SimulationResult:
    if not panel.effective_from <= scan_from < scan_to <= panel.effective_to:
        raise ValueError("検証期間は共通検証期間の範囲内で指定してください")
    _validate_risk_options(
        stop_loss=stop_loss,
        take_profit=take_profit,
        trailing_stop=trailing_stop,
        trailing_stop_pct=trailing_stop_pct,
        maximum_holding_minutes=maximum_holding_minutes,
    )
    start_index = bisect_left(panel.times, scan_from)
    end_index = bisect_left(panel.times, scan_to)
    if start_index >= end_index:
        raise ValueError("指定期間に検証可能なM5バーがありません")
    force_bar = _force_close_bar(panel.series["M5"], scan_to)
    force_price = force_bar.close
    force_spread = force_bar.spread

    profits: list[float] = []
    spread_adjusted_profits: list[float] = []
    holding_minutes: list[int] = []
    trades: list[Mapping[str, object]] = []
    forced_close_count = 0
    cursor = start_index - 1
    first_entry = True
    while True:
        entry_indices = (
            strategy.entry_rule.initial_indices
            if first_entry
            else strategy.entry_rule.reentry_indices
        )
        entry_position = bisect_right(entry_indices, cursor)
        if entry_position >= len(entry_indices):
            break
        entry_index = entry_indices[entry_position]
        if entry_index < start_index:
            cursor = start_index - 1
            first_entry = True
            continue
        if entry_index >= end_index:
            break

        exit_position = bisect_right(strategy.exit_rule.indices, entry_index)
        signal_exit_index: Optional[int] = None
        if exit_position < len(strategy.exit_rule.indices):
            candidate = strategy.exit_rule.indices[exit_position]
            if candidate < end_index:
                signal_exit_index = candidate

        entry_bar = panel.bars[entry_index]
        risk_exit = _find_risk_exit(
            panel=panel,
            direction=strategy.direction,
            entry_index=entry_index,
            end_index=(
                signal_exit_index if signal_exit_index is not None else end_index
            ),
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=trailing_stop,
            trailing_stop_pct=trailing_stop_pct,
            maximum_holding_minutes=maximum_holding_minutes,
        )
        exit_index: Optional[int]
        exit_reason: str
        if risk_exit is not None:
            exit_index, exit_price, exit_reason = risk_exit
            exit_bar = panel.bars[exit_index]
            exit_time = exit_bar.time
            exit_spread = exit_bar.spread
            forced = False
        elif signal_exit_index is not None:
            exit_index = signal_exit_index
            exit_bar = panel.bars[exit_index]
            exit_time = exit_bar.time
            exit_price = exit_bar.open
            exit_spread = exit_bar.spread
            exit_reason = "signal"
            forced = False
        else:
            exit_index = None
            exit_time = scan_to
            exit_price = force_price
            exit_spread = force_spread
            exit_reason = "forced"
            forced = True
            forced_close_count += 1

        if strategy.direction == "long":
            gross_profit = exit_price - entry_bar.open
            spread_profit = exit_price - (
                entry_bar.open + entry_bar.spread * point_size
            )
        else:
            gross_profit = entry_bar.open - exit_price
            spread_profit = entry_bar.open - (exit_price + exit_spread * point_size)
        profits.append(gross_profit)
        spread_adjusted_profits.append(spread_profit)
        duration = int((exit_time - entry_bar.time).total_seconds() / 60)
        holding_minutes.append(duration)
        if collect_trades:
            trades.append(
                {
                    "sequence": len(trades) + 1,
                    "entry_time": _format_datetime(entry_bar.time),
                    "entry_price": _round_metric(entry_bar.open),
                    "exit_time": _format_datetime(exit_time),
                    "exit_price": _round_metric(exit_price),
                    "gross_profit": _round_metric(gross_profit),
                    "spread_adjusted_profit": _round_metric(spread_profit),
                    "holding_minutes": duration,
                    "exit_reason": exit_reason,
                    "forced": forced,
                }
            )
        if forced:
            break
        assert exit_index is not None
        cursor = exit_index
        first_entry = False

    metrics = _build_metrics(
        profits=profits,
        spread_adjusted_profits=spread_adjusted_profits,
        holding_minutes=holding_minutes,
        forced_close_count=forced_close_count,
    )
    return SimulationResult(metrics=metrics, trades=tuple(trades))


def simulate_single_position_strategies(
    specifications: Sequence[StrategySimulationSpec],
    scan_from: datetime,
    scan_to: datetime,
    point_size: float = 0.01,
    collect_trades: bool = False,
) -> SimulationResult:
    if len(specifications) < 2:
        raise ValueError("1ポジション比較には2つ以上の戦略を指定してください")
    reference_panel = specifications[0].panel
    if any(
        specification.panel.times != reference_panel.times
        for specification in specifications
    ):
        raise ValueError("各戦略の検証時刻は一致させてください")
    if (
        not reference_panel.effective_from
        <= scan_from
        < scan_to
        <= reference_panel.effective_to
    ):
        raise ValueError("検証期間は共通検証期間の範囲内で指定してください")
    strategy_ids = [
        specification.strategy.strategy_id for specification in specifications
    ]
    if len(set(strategy_ids)) != len(strategy_ids):
        raise ValueError("戦略IDは重複しないように指定してください")
    for specification in specifications:
        if (
            specification.strategy.direction
            != specification.strategy.entry_rule.direction
        ):
            raise ValueError("戦略とエントリー条件の方向が一致していません")
        _validate_risk_options(
            stop_loss=specification.stop_loss,
            take_profit=specification.take_profit,
            trailing_stop=specification.trailing_stop,
            trailing_stop_pct=specification.trailing_stop_pct,
            maximum_holding_minutes=specification.maximum_holding_minutes,
        )

    start_index = bisect_left(reference_panel.times, scan_from)
    end_index = bisect_left(reference_panel.times, scan_to)
    force_bar = _force_close_bar(reference_panel.series["M5"], scan_to)
    profits: list[float] = []
    spread_adjusted_profits: list[float] = []
    holding_minutes: list[int] = []
    trades: list[Mapping[str, object]] = []
    forced_close_count = 0
    cursor = start_index - 1
    traded_strategy_ids = set()

    while True:
        candidates = []
        for specification in specifications:
            strategy = specification.strategy
            entry_indices = (
                strategy.entry_rule.reentry_indices
                if strategy.strategy_id in traded_strategy_ids
                else strategy.entry_rule.initial_indices
            )
            entry_position = bisect_right(entry_indices, cursor)
            if entry_position >= len(entry_indices):
                continue
            entry_index = entry_indices[entry_position]
            if entry_index < end_index:
                candidates.append((entry_index, specification))
        if not candidates:
            break
        next_index = min(candidate[0] for candidate in candidates)
        next_specifications = [
            specification
            for entry_index, specification in candidates
            if entry_index == next_index
        ]
        if len(next_specifications) > 1:
            cursor = next_index
            continue

        specification = next_specifications[0]
        panel = specification.panel
        strategy = specification.strategy
        entry_index = next_index
        signal_position = bisect_right(strategy.exit_rule.indices, entry_index)
        signal_exit_index: Optional[int] = None
        if signal_position < len(strategy.exit_rule.indices):
            candidate = strategy.exit_rule.indices[signal_position]
            if candidate < end_index:
                signal_exit_index = candidate
        entry_bar = panel.bars[entry_index]
        risk_exit = _find_risk_exit(
            panel=panel,
            direction=strategy.direction,
            entry_index=entry_index,
            end_index=(
                signal_exit_index if signal_exit_index is not None else end_index
            ),
            stop_loss=specification.stop_loss,
            take_profit=specification.take_profit,
            trailing_stop=specification.trailing_stop,
            trailing_stop_pct=specification.trailing_stop_pct,
            maximum_holding_minutes=specification.maximum_holding_minutes,
        )
        exit_index: Optional[int]
        if risk_exit is not None:
            exit_index, exit_price, exit_reason = risk_exit
            exit_bar = panel.bars[exit_index]
            exit_time = exit_bar.time
            exit_spread = exit_bar.spread
            forced = False
        elif signal_exit_index is not None:
            exit_index = signal_exit_index
            exit_bar = panel.bars[exit_index]
            exit_time = exit_bar.time
            exit_price = exit_bar.open
            exit_spread = exit_bar.spread
            exit_reason = "signal"
            forced = False
        else:
            exit_index = None
            exit_time = scan_to
            exit_price = force_bar.close
            exit_spread = force_bar.spread
            exit_reason = "forced"
            forced = True
            forced_close_count += 1

        if strategy.direction == "long":
            gross_profit = exit_price - entry_bar.open
            spread_profit = exit_price - (
                entry_bar.open + entry_bar.spread * point_size
            )
        else:
            gross_profit = entry_bar.open - exit_price
            spread_profit = entry_bar.open - (exit_price + exit_spread * point_size)
        duration = int((exit_time - entry_bar.time).total_seconds() / 60)
        profits.append(gross_profit)
        spread_adjusted_profits.append(spread_profit)
        holding_minutes.append(duration)
        if collect_trades:
            trades.append(
                {
                    "sequence": len(trades) + 1,
                    "strategy_id": strategy.strategy_id,
                    "direction": strategy.direction,
                    "entry_time": _format_datetime(entry_bar.time),
                    "entry_price": _round_metric(entry_bar.open),
                    "exit_time": _format_datetime(exit_time),
                    "exit_price": _round_metric(exit_price),
                    "gross_profit": _round_metric(gross_profit),
                    "spread_adjusted_profit": _round_metric(spread_profit),
                    "holding_minutes": duration,
                    "exit_reason": exit_reason,
                    "forced": forced,
                }
            )
        traded_strategy_ids.add(strategy.strategy_id)
        if forced:
            break
        assert exit_index is not None
        cursor = exit_index

    return SimulationResult(
        metrics=_build_metrics(
            profits=profits,
            spread_adjusted_profits=spread_adjusted_profits,
            holding_minutes=holding_minutes,
            forced_close_count=forced_close_count,
        ),
        trades=tuple(trades),
    )


def _validate_risk_options(
    stop_loss: Optional[float],
    take_profit: Optional[float],
    trailing_stop: Optional[float],
    trailing_stop_pct: Optional[float],
    maximum_holding_minutes: Optional[int],
) -> None:
    named_values = {
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "trailing_stop": trailing_stop,
    }
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
        for value in named_values.values()
        if value is not None
    ):
        raise ValueError("損切り・利確・トレーリング幅は0より大きく指定してください")
    if maximum_holding_minutes is not None and (
        isinstance(maximum_holding_minutes, bool)
        or not isinstance(maximum_holding_minutes, int)
        or maximum_holding_minutes <= 0
    ):
        raise ValueError("最大保有時間は1分以上の整数で指定してください")
    if trailing_stop_pct is not None and (
        isinstance(trailing_stop_pct, bool)
        or not isinstance(trailing_stop_pct, (int, float))
        or not 0 < trailing_stop_pct < 100
    ):
        raise ValueError("追従決済率は0より大きく100未満で指定してください")


def _find_risk_exit(
    panel: ResearchPanel,
    direction: Direction,
    entry_index: int,
    end_index: int,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    trailing_stop: Optional[float],
    trailing_stop_pct: Optional[float],
    maximum_holding_minutes: Optional[int],
) -> Optional[Tuple[int, float, str]]:
    if (
        stop_loss is None
        and take_profit is None
        and trailing_stop is None
        and trailing_stop_pct is None
        and maximum_holding_minutes is None
    ):
        return None
    entry_bar = panel.bars[entry_index]
    favorable_price = entry_bar.open
    for index in range(entry_index, end_index):
        bar = panel.bars[index]
        held_minutes = int((bar.time - entry_bar.time).total_seconds() / 60)
        if (
            maximum_holding_minutes is not None
            and held_minutes >= maximum_holding_minutes
        ):
            return index, bar.open, "maximum_holding"

        if direction == "long":
            stop_prices = []
            if stop_loss is not None:
                stop_prices.append(entry_bar.open - stop_loss)
            if trailing_stop is not None:
                stop_prices.append(favorable_price - trailing_stop)
            if trailing_stop_pct is not None:
                stop_prices.append(favorable_price * (1.0 - trailing_stop_pct / 100.0))
            active_stop = max(stop_prices) if stop_prices else None
            target = entry_bar.open + take_profit if take_profit is not None else None
            if active_stop is not None and bar.open <= active_stop:
                return index, bar.open, "stop"
            if target is not None and bar.open >= target:
                return index, target, "take_profit"
            if active_stop is not None and bar.low <= active_stop:
                return index, active_stop, "stop"
            if target is not None and bar.high >= target:
                return index, target, "take_profit"
            favorable_price = max(favorable_price, bar.high)
        else:
            stop_prices = []
            if stop_loss is not None:
                stop_prices.append(entry_bar.open + stop_loss)
            if trailing_stop is not None:
                stop_prices.append(favorable_price + trailing_stop)
            if trailing_stop_pct is not None:
                stop_prices.append(favorable_price * (1.0 + trailing_stop_pct / 100.0))
            active_stop = min(stop_prices) if stop_prices else None
            target = entry_bar.open - take_profit if take_profit is not None else None
            if active_stop is not None and bar.open >= active_stop:
                return index, bar.open, "stop"
            if target is not None and bar.open <= target:
                return index, target, "take_profit"
            if active_stop is not None and bar.high >= active_stop:
                return index, active_stop, "stop"
            if target is not None and bar.low <= target:
                return index, target, "take_profit"
            favorable_price = min(favorable_price, bar.low)
    return None


def _validate_options(
    moving_average_periods: Sequence[int],
    scan_from: Optional[datetime],
    scan_to: Optional[datetime],
    end_bar_shift: int,
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
    if isinstance(end_bar_shift, bool) or end_bar_shift < 0:
        raise ValueError("末尾バーシフトは0以上の整数で指定してください")
    if scan_from is not None and scan_to is not None and scan_from >= scan_to:
        raise ValueError("走査終了日時は走査開始日時より後に指定してください")
    if not expected_symbol.strip():
        raise ValueError("expected_symbol は空でない文字列で指定してください")
    return short_period, middle_period, long_period


def _completed_indices(
    series: PeriodSeries,
    grid_times: Sequence[datetime],
) -> Tuple[int, ...]:
    last_usable = len(series.usable_bars) - 1
    delta = timedelta(minutes=PERIOD_MINUTES[series.period])
    return tuple(
        min(bisect_right(series.times, time - delta) - 1, last_usable)
        for time in grid_times
    )


def _ordered_state(
    index: int,
    short_values: Sequence[Optional[float]],
    middle_values: Sequence[Optional[float]],
    long_values: Sequence[Optional[float]],
    direction: Direction,
) -> bool:
    if index < 0:
        return False
    values = (short_values[index], middle_values[index], long_values[index])
    if any(value is None for value in values):
        return False
    short_value, middle_value, long_value = cast(Tuple[float, float, float], values)
    if direction == "long":
        return short_value > middle_value > long_value
    return short_value < middle_value < long_value


def _relation_state(
    index: int,
    short_values: Sequence[Optional[float]],
    long_values: Sequence[Optional[float]],
    direction: Direction,
) -> bool:
    if index < 0:
        return False
    values = (short_values[index], long_values[index])
    if any(value is None for value in values):
        return False
    short_value, long_value = cast(Tuple[float, float], values)
    if direction == "long":
        return short_value > long_value
    return short_value < long_value


def _cross_indices(
    series: PeriodSeries,
    completed_indices: Sequence[int],
    short_period: int,
    comparison_period: int,
    cross_type: str,
) -> Sequence[int]:
    short_values = series.moving_averages[short_period]
    comparison_values = series.moving_averages[comparison_period]
    result = []
    previous_completed = completed_indices[0] - 1
    for grid_index, completed in enumerate(completed_indices):
        for bar_index in range(max(previous_completed + 1, 1), completed + 1):
            values = (
                short_values[bar_index - 1],
                comparison_values[bar_index - 1],
                short_values[bar_index],
                comparison_values[bar_index],
            )
            if any(value is None for value in values):
                continue
            previous_short, previous_comparison, current_short, current_comparison = (
                cast(Tuple[float, float, float, float], values)
            )
            golden = (
                previous_short <= previous_comparison
                and current_short > current_comparison
            )
            death = (
                previous_short >= previous_comparison
                and current_short < current_comparison
            )
            if (cross_type == "golden_cross" and golden) or (
                cross_type == "death_cross" and death
            ):
                result.append(grid_index)
                break
        previous_completed = max(previous_completed, completed)
    return result


def _filtered_cross_entry_rule(
    panel: ResearchPanel,
    direction: Direction,
    signal_period: str,
    comparison_period: int,
    confirmation_periods: Sequence[str],
    rule_id: str,
    label: str,
) -> EntryRule:
    trend_states = panel.trend_up if direction == "long" else panel.trend_down
    cross_type = "golden_cross" if direction == "long" else "death_cross"
    base_indices = panel.crosses[(signal_period, comparison_period, cross_type)]
    indices = tuple(
        index
        for index in base_indices
        if all(trend_states[period][index] for period in confirmation_periods)
    )
    return EntryRule(
        rule_id=rule_id,
        label=label,
        direction=direction,
        initial_indices=indices,
        reentry_indices=indices,
    )


def _transition_indices(
    state: Sequence[bool],
    previous_value: bool,
    current_value: bool,
) -> Tuple[int, ...]:
    return tuple(
        index
        for index in range(1, len(state))
        if state[index - 1] is previous_value and state[index] is current_value
    )


def _true_indices(state: Sequence[bool]) -> Tuple[int, ...]:
    return tuple(index for index, value in enumerate(state) if value)


def _force_close_bar(series: PeriodSeries, scan_to: datetime) -> MarketBar:
    target = scan_to - timedelta(minutes=PERIOD_MINUTES[series.period])
    index = bisect_right(series.times, target) - 1
    index = min(index, len(series.usable_bars) - 1)
    if index < 0:
        raise ValueError("強制決済に使用できるM5確定足がありません")
    return series.usable_bars[index]


def _build_metrics(
    profits: Sequence[float],
    spread_adjusted_profits: Sequence[float],
    holding_minutes: Sequence[int],
    forced_close_count: int,
) -> Mapping[str, object]:
    return {
        "trade_count": len(profits),
        "forced_close_count": forced_close_count,
        "average_holding_minutes": (
            _round_metric(mean(holding_minutes)) if holding_minutes else None
        ),
        "maximum_holding_minutes": max(holding_minutes) if holding_minutes else 0,
        "gross": _profit_metrics(profits),
        "spread_adjusted": _profit_metrics(spread_adjusted_profits),
    }


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
    profit_factor = sum(wins) / abs(sum(losses)) if losses else None
    total_profit = sum(profits)
    recovery_factor = total_profit / maximum_drawdown if maximum_drawdown > 0 else None
    return {
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": len(profits) - len(wins) - len(losses),
        "win_rate_pct": (
            _round_metric(len(wins) / len(profits) * 100) if profits else 0.0
        ),
        "total_profit": _round_metric(total_profit),
        "average_profit": _round_metric(mean(profits)) if profits else None,
        "maximum_profit": _round_metric(max(profits)) if profits else None,
        "minimum_profit": _round_metric(min(profits)) if profits else None,
        "profit_factor": (
            _round_metric(profit_factor) if profit_factor is not None else None
        ),
        "maximum_drawdown": _round_metric(maximum_drawdown),
        "recovery_factor": (
            _round_metric(recovery_factor) if recovery_factor is not None else None
        ),
    }


def _validate_direction(direction: str) -> None:
    if direction not in ("long", "short"):
        raise ValueError("direction は long または short で指定してください")


def _round_metric(value: float) -> float:
    return round(float(value), 8)


def _format_datetime(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)
