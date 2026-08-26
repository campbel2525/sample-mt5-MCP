from datetime import datetime, timedelta
from typing import Optional, cast

import pytest

from services.backtest_service import run_exit_backtest
from services.cross_scan_service import CrossScanResult, CrossType, MovingAverageCross

BASE_TIME = datetime(2026, 1, 5, 0, 0)


def _dataset(
    period: str,
    minutes: int,
    count: int,
    high_by_index: Optional[dict[int, float]] = None,
) -> dict[str, object]:
    high_by_index = high_by_index or {}
    history = []
    for index in range(count):
        price = 100.0
        history.append(
            {
                "time": (BASE_TIME + timedelta(minutes=minutes * index)).strftime(
                    "%Y.%m.%d %H:%M:%S"
                ),
                "open": price,
                "high": high_by_index.get(index, price + 0.1),
                "low": price - 0.1,
                "close": price,
                "tick_volume": 100,
                "spread": 2,
            }
        )
    return {"symbol": "GOLD", "period": period, "history": history}


def _cross(
    period: str,
    bar_time: datetime,
    cross_type: CrossType,
    comparison_period: int,
) -> MovingAverageCross:
    return MovingAverageCross(
        symbol="GOLD",
        period=period,
        bar_time=bar_time,
        cross_type=cross_type,
        short_period=1,
        comparison_period=comparison_period,
        previous_short_ma=99.0,
        previous_comparison_ma=100.0,
        current_short_ma=101.0,
        current_comparison_ma=100.0,
        close=100.0,
    )


def _cross_result(
    period: str,
    minutes: int,
    bar_count: int,
    crosses: tuple[MovingAverageCross, ...] = (),
) -> CrossScanResult:
    return CrossScanResult(
        symbol="GOLD",
        period=period,
        first_bar_time=BASE_TIME,
        last_bar_time=BASE_TIME + timedelta(minutes=minutes * (bar_count - 1)),
        bar_count=bar_count,
        crosses=crosses,
    )


def _inputs() -> tuple[list[dict[str, object]], list[CrossScanResult]]:
    counts = {"M5": 49, "M15": 17, "M30": 9}
    datasets = [
        _dataset("M5", 5, counts["M5"], high_by_index={24: 102.0}),
        _dataset("M15", 15, counts["M15"]),
        _dataset("M30", 30, counts["M30"]),
    ]
    entry_cross = _cross(
        period="M30",
        bar_time=BASE_TIME + timedelta(minutes=90),
        cross_type="golden_cross",
        comparison_period=3,
    )
    death_cross = _cross(
        period="M5",
        bar_time=BASE_TIME + timedelta(minutes=130),
        cross_type="death_cross",
        comparison_period=2,
    )
    results = [
        _cross_result("M5", 5, counts["M5"], (death_cross,)),
        _cross_result("M15", 15, counts["M15"]),
        _cross_result("M30", 30, counts["M30"], (entry_cross,)),
    ]
    return datasets, results


def _run() -> tuple[dict[str, object], dict[str, object]]:
    datasets, results = _inputs()
    output = run_exit_backtest(
        datasets=datasets,
        cross_results=results,
        moving_average_periods=(1, 2, 3),
        end_bar_shift=1,
    )
    return dict(output.trades), dict(output.summary)


def test_backtest_builds_all_62_exit_strategies() -> None:
    trades, summary = _run()
    strategies = cast(list[dict[str, object]], summary["strategies"])

    assert summary["strategy_count"] == 62
    settings = cast(dict[str, object], summary["settings"])
    assert settings["entry_condition"] == "M30_SMA1_SMA3_golden_cross"
    assert settings["cost_mode"] == "none"
    assert settings["spread_assumption"] is None
    assert len(strategies) == 62
    assert summary["trade_count"] == trades["trade_count"]
    assert sorted(cast(int, strategy["rank"]) for strategy in strategies) == list(
        range(1, 63)
    )
    assert sorted(cast(int, strategy["rank_first"]) for strategy in strategies) == list(
        range(1, 63)
    )
    assert sorted(
        cast(int, strategy["rank_second"]) for strategy in strategies
    ) == list(range(1, 63))


def test_backtest_builds_only_six_moving_average_exit_strategies() -> None:
    datasets, results = _inputs()
    output = run_exit_backtest(
        datasets=datasets,
        cross_results=results,
        moving_average_periods=(1, 2, 3),
        moving_average_exits_only=True,
        end_bar_shift=1,
    )
    trades = dict(output.trades)
    summary = dict(output.summary)
    strategies = cast(list[dict[str, object]], summary["strategies"])

    assert summary["strategy_count"] == 6
    assert [strategy["strategy_id"] for strategy in strategies] == [
        "death_M5_1_2",
        "death_M5_1_3",
        "death_M15_1_2",
        "death_M15_1_3",
        "death_M30_1_2",
        "death_M30_1_3",
    ]
    assert {
        trade["exit_reason"]
        for trade in cast(list[dict[str, object]], trades["trades"])
    } <= {"death_cross", "force_close"}
    settings = cast(dict[str, object], summary["settings"])
    assert settings["exit_strategy_mode"] == "moving_average_only"
    assert "profit_targets_pct" not in settings
    assert "position_sizing" not in settings
    assert "ranking_basis" not in settings
    assert "ranking_method" not in settings
    for strategy in strategies:
        assert "rank" not in strategy
        assert "rank_overall" not in strategy
        metrics = cast(
            dict[str, object],
            cast(dict[str, object], strategy["periods"])["overall"],
        )["gross"]
        assert isinstance(metrics, dict)
        assert "compounded_return_pct" not in metrics
        assert "maximum_drawdown_pct" not in metrics


def test_backtest_executes_crosses_at_next_bar_open() -> None:
    trades, _ = _run()
    trade_items = cast(list[dict[str, object]], trades["trades"])
    selected = next(
        trade for trade in trade_items if trade["strategy_id"] == "death_M5_1_2"
    )

    assert selected["entry_signal_bar_time"] == "2026.01.05 01:30:00"
    assert selected["entry_time"] == "2026.01.05 02:00:00"
    assert selected["exit_signal_bar_time"] == "2026.01.05 02:10:00"
    assert selected["exit_time"] == "2026.01.05 02:15:00"
    assert selected["entry_price"] == 100.0
    assert "entry_price_with_spread" not in selected
    assert "spread_adjusted_return_pct" not in selected


def test_backtest_detects_take_profit_from_m5_high() -> None:
    trades, _ = _run()
    trade_items = cast(list[dict[str, object]], trades["trades"])
    selected = next(
        trade for trade in trade_items if trade["strategy_id"] == "take_profit_1_00"
    )

    assert selected["exit_reason"] == "take_profit"
    assert selected["exit_time"] == "2026.01.05 02:05:00"
    assert selected["exit_price"] == 101.0


def test_backtest_rejects_missing_required_timeframe() -> None:
    datasets, results = _inputs()

    with pytest.raises(ValueError, match="M15"):
        run_exit_backtest(
            datasets=[item for item in datasets if item["period"] != "M15"],
            cross_results=results,
            moving_average_periods=(1, 2, 3),
        )


def test_backtest_rejects_duplicate_bar_time() -> None:
    datasets, results = _inputs()
    history = cast(list[dict[str, object]], datasets[0]["history"])
    history[2]["time"] = history[1]["time"]

    with pytest.raises(ValueError, match="重複日時"):
        run_exit_backtest(
            datasets=datasets,
            cross_results=results,
            moving_average_periods=(1, 2, 3),
        )


def test_backtest_rejects_indicator_settings_other_than_sma_close() -> None:
    datasets, results = _inputs()

    with pytest.raises(ValueError, match="SMA"):
        run_exit_backtest(
            datasets=datasets,
            cross_results=results,
            moving_average_periods=(1, 2, 3),
            moving_average_method="EMA",
        )
    with pytest.raises(ValueError, match="CLOSE"):
        run_exit_backtest(
            datasets=datasets,
            cross_results=results,
            moving_average_periods=(1, 2, 3),
            applied_price="OPEN",
        )
