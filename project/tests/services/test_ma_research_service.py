from datetime import datetime, timedelta
from typing import cast

from services.ma_research_service import (
    EntryRule,
    ExitRule,
    ResearchStrategy,
    StrategySimulationSpec,
    build_entry_rules,
    build_exit_rules,
    prepare_research_panel,
    simulate_single_position_strategies,
    simulate_strategy,
)

BASE_TIME = datetime(2026, 1, 5)


def _dataset(period: str, minutes: int, count: int) -> dict[str, object]:
    history = []
    for index in range(count):
        price = 100.0 if index < 60 else 100.0 + (index - 59) * 2.0
        history.append(
            {
                "time": (BASE_TIME + timedelta(minutes=minutes * index)).strftime(
                    "%Y.%m.%d %H:%M:%S"
                ),
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "tick_volume": 100,
                "spread": 20,
            }
        )
    return {"symbol": "GOLD", "period": period, "history": history}


def _datasets() -> list[dict[str, object]]:
    return [
        _dataset("M5", 5, 960),
        _dataset("M15", 15, 320),
        _dataset("M30", 30, 160),
        _dataset("H1", 60, 80),
    ]


def test_research_panel_builds_perfect_order_rules_without_lookahead() -> None:
    panel = prepare_research_panel(
        datasets=_datasets(),
        moving_average_periods=(5, 20, 60),
        scan_from=BASE_TIME,
        scan_to=BASE_TIME + timedelta(hours=80),
    )
    entry_rules = build_entry_rules(panel, "long")
    exit_rules = build_exit_rules(panel, "long")
    all_periods = next(
        rule for rule in entry_rules if rule.rule_id == "po_complete_M5_M15_M30_H1"
    )

    assert panel.effective_from == BASE_TIME + timedelta(hours=60)
    assert panel.effective_to == BASE_TIME + timedelta(hours=79)
    assert all_periods.initial_indices
    first_entry = all_periods.initial_indices[0]
    assert panel.times[first_entry] > panel.effective_from
    assert all(
        panel.bullish_order[period][first_entry]
        for period in ("M5", "M15", "M30", "H1")
    )
    assert len(entry_rules) > 50
    assert len(exit_rules) > 20


def test_simulation_applies_long_entry_spread_and_forces_last_close() -> None:
    panel = prepare_research_panel(
        datasets=_datasets(),
        moving_average_periods=(5, 20, 60),
        scan_from=BASE_TIME,
        scan_to=BASE_TIME + timedelta(hours=80),
    )
    entry_rule = next(
        rule
        for rule in build_entry_rules(panel, "long")
        if rule.rule_id == "po_complete_M5_M15_M30_H1"
    )
    strategy = ResearchStrategy(
        strategy_id="test_long",
        direction="long",
        entry_rule=entry_rule,
        exit_rule=ExitRule(
            rule_id="never",
            label="決済なし",
            direction="long",
            indices=(),
        ),
    )

    result = simulate_strategy(
        panel=panel,
        strategy=strategy,
        scan_from=panel.effective_from,
        scan_to=panel.effective_to,
        collect_trades=True,
    )

    metrics = cast(dict[str, object], result.metrics)
    gross = cast(dict[str, object], metrics["gross"])
    spread_adjusted = cast(dict[str, object], metrics["spread_adjusted"])
    assert metrics["trade_count"] == 1
    assert metrics["forced_close_count"] == 1
    assert len(result.trades) == 1
    assert result.trades[0]["forced"] is True
    assert (
        cast(float, spread_adjusted["total_profit"])
        == cast(float, gross["total_profit"]) - 0.2
    )


def test_simulation_applies_short_exit_spread() -> None:
    panel = prepare_research_panel(
        datasets=_datasets(),
        moving_average_periods=(5, 20, 60),
        scan_from=BASE_TIME,
        scan_to=BASE_TIME + timedelta(hours=80),
    )
    strategy = ResearchStrategy(
        strategy_id="test_short",
        direction="short",
        entry_rule=EntryRule(
            rule_id="entry",
            label="entry",
            direction="short",
            initial_indices=(1,),
            reentry_indices=(),
        ),
        exit_rule=ExitRule(
            rule_id="exit",
            label="exit",
            direction="short",
            indices=(2,),
        ),
    )

    result = simulate_strategy(
        panel=panel,
        strategy=strategy,
        scan_from=panel.effective_from,
        scan_to=panel.effective_to,
        collect_trades=True,
    )

    trade = result.trades[0]
    assert (
        cast(float, trade["spread_adjusted_profit"])
        == cast(float, trade["gross_profit"]) - 0.2
    )


def test_simulation_applies_take_profit_before_signal_exit() -> None:
    panel = prepare_research_panel(
        datasets=_datasets(),
        moving_average_periods=(5, 20, 60),
        scan_from=BASE_TIME,
        scan_to=BASE_TIME + timedelta(hours=80),
    )
    strategy = ResearchStrategy(
        strategy_id="test_take_profit",
        direction="long",
        entry_rule=EntryRule(
            rule_id="entry",
            label="entry",
            direction="long",
            initial_indices=(1,),
            reentry_indices=(),
        ),
        exit_rule=ExitRule(
            rule_id="exit",
            label="exit",
            direction="long",
            indices=(10,),
        ),
    )

    result = simulate_strategy(
        panel=panel,
        strategy=strategy,
        scan_from=panel.effective_from,
        scan_to=panel.effective_to,
        collect_trades=True,
        take_profit=3.0,
    )

    trade = result.trades[0]
    assert trade["exit_reason"] == "take_profit"
    assert trade["gross_profit"] == 3.0
    assert trade["forced"] is False


def test_simulation_applies_maximum_holding_time() -> None:
    panel = prepare_research_panel(
        datasets=_datasets(),
        moving_average_periods=(5, 20, 60),
        scan_from=BASE_TIME,
        scan_to=BASE_TIME + timedelta(hours=80),
    )
    strategy = ResearchStrategy(
        strategy_id="test_maximum_holding",
        direction="long",
        entry_rule=EntryRule(
            rule_id="entry",
            label="entry",
            direction="long",
            initial_indices=(1,),
            reentry_indices=(),
        ),
        exit_rule=ExitRule(
            rule_id="never",
            label="never",
            direction="long",
            indices=(),
        ),
    )

    result = simulate_strategy(
        panel=panel,
        strategy=strategy,
        scan_from=panel.effective_from,
        scan_to=panel.effective_to,
        collect_trades=True,
        maximum_holding_minutes=10,
    )

    trade = result.trades[0]
    assert trade["exit_reason"] == "maximum_holding"
    assert trade["holding_minutes"] == 10


def test_simulation_applies_percentage_trailing_stop() -> None:
    panel = prepare_research_panel(
        datasets=_datasets(),
        moving_average_periods=(5, 20, 60),
        scan_from=BASE_TIME,
        scan_to=BASE_TIME + timedelta(hours=80),
    )
    strategy = ResearchStrategy(
        strategy_id="test_percentage_trailing",
        direction="short",
        entry_rule=EntryRule("entry", "entry", "short", (1,), ()),
        exit_rule=ExitRule("never", "never", "short", ()),
    )

    result = simulate_strategy(
        panel=panel,
        strategy=strategy,
        scan_from=panel.effective_from,
        scan_to=panel.effective_to,
        collect_trades=True,
        trailing_stop_pct=1.0,
    )

    trade = result.trades[0]
    assert trade["exit_reason"] == "stop"
    assert trade["forced"] is False


def test_single_position_simulation_ignores_entry_while_position_is_open() -> None:
    panel = prepare_research_panel(
        datasets=_datasets(),
        moving_average_periods=(5, 20, 60),
        scan_from=BASE_TIME,
        scan_to=BASE_TIME + timedelta(hours=80),
    )
    long_strategy = ResearchStrategy(
        strategy_id="long",
        direction="long",
        entry_rule=EntryRule("entry", "entry", "long", (1,), ()),
        exit_rule=ExitRule("exit", "exit", "long", (4,)),
    )
    short_strategy = ResearchStrategy(
        strategy_id="short",
        direction="short",
        entry_rule=EntryRule("entry", "entry", "short", (2,), ()),
        exit_rule=ExitRule("exit", "exit", "short", (3,)),
    )

    result = simulate_single_position_strategies(
        specifications=(
            StrategySimulationSpec(panel=panel, strategy=long_strategy),
            StrategySimulationSpec(panel=panel, strategy=short_strategy),
        ),
        scan_from=panel.effective_from,
        scan_to=panel.effective_to,
        collect_trades=True,
    )

    assert result.metrics["trade_count"] == 1
    assert result.trades[0]["direction"] == "long"
