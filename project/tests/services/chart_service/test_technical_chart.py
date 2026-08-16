import math
from typing import Callable

from services.chart_service import (
    TechnicalChart,
    normalize_applied_price,
    normalize_moving_average_method,
)


def _history_from_closes(closes: list[float]) -> list[object]:
    return [
        {
            "time": f"2026.08.15 {index:02d}:00:00",
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "tick_volume": 100 + index,
            "spread": 20,
        }
        for index, close in enumerate(closes)
    ]


def _chart_from_closes(
    closes: list[float],
    moving_average_method: str = "SMA",
    applied_price: str = "CLOSE",
) -> TechnicalChart:
    return TechnicalChart(
        _history_from_closes(closes),
        moving_average_method=moving_average_method,
        applied_price=applied_price,
    )


def _valid_bar() -> dict[str, object]:
    return {
        "time": "2026.08.15 19:00:00",
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "tick_volume": 193,
        "spread": 20,
    }


def _assert_value_error(
    action: Callable[[], object],
    expected_message: str,
) -> None:
    try:
        action()
    except ValueError as exc:
        assert expected_message in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_technical_chart_accepts_history_and_sorts_by_time() -> None:
    history = _history_from_closes([100.0, 101.0])
    history.reverse()

    chart = TechnicalChart(history)

    assert len(chart.history) == 2
    assert chart.history[0].time.hour == 0
    assert chart.history[1].time.hour == 1


def test_technical_chart_converts_numeric_prices_to_float() -> None:
    bar = _valid_bar()
    bar["open"] = 100
    bar["close"] = 101

    chart = TechnicalChart([bar])

    assert chart.history[0].open == 100.0
    assert isinstance(chart.history[0].open, float)
    assert chart.history[0].close == 101.0
    assert isinstance(chart.history[0].close, float)


def test_technical_chart_rejects_empty_history() -> None:
    _assert_value_error(
        lambda: TechnicalChart([]),
        "history は1件以上の配列で指定してください",
    )


def test_technical_chart_rejects_non_object_history_item() -> None:
    _assert_value_error(
        lambda: TechnicalChart(["invalid"]),
        "history[0] はオブジェクトで指定してください",
    )


def test_technical_chart_rejects_missing_time() -> None:
    bar = _valid_bar()
    del bar["time"]

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "history[0].time は空でない文字列で指定してください",
    )


def test_technical_chart_rejects_blank_time() -> None:
    bar = _valid_bar()
    bar["time"] = "  "

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "history[0].time は空でない文字列で指定してください",
    )


def test_technical_chart_rejects_invalid_time_format() -> None:
    bar = _valid_bar()
    bar["time"] = "2026-08-15T19:00:00"

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "history[0].time は YYYY.MM.DD HH:MM:SS 形式",
    )


def test_technical_chart_rejects_missing_ohlc_value() -> None:
    bar = _valid_bar()
    del bar["close"]

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "history[0].close は数値で指定してください",
    )


def test_technical_chart_rejects_boolean_ohlc_value() -> None:
    bar = _valid_bar()
    bar["close"] = True

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "history[0].close は数値で指定してください",
    )


def test_technical_chart_rejects_non_finite_ohlc_values() -> None:
    for value in (math.nan, math.inf, -math.inf):
        bar = _valid_bar()
        bar["close"] = value
        _assert_value_error(
            lambda bar=bar: TechnicalChart([bar]),
            "history[0].close は有限の数値で指定してください",
        )


def test_technical_chart_rejects_low_above_high() -> None:
    bar = _valid_bar()
    bar["low"] = 103.0

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "low が high を超えています",
    )


def test_technical_chart_rejects_high_below_open_or_close() -> None:
    bar = _valid_bar()
    bar["high"] = 100.5

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "high が始値または終値未満です",
    )


def test_technical_chart_rejects_low_above_open_or_close() -> None:
    bar = _valid_bar()
    bar["low"] = 100.5

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "low が始値または終値より大きいです",
    )


def test_technical_chart_accepts_ohlc_values_on_high_low_boundaries() -> None:
    bar = _valid_bar()
    bar["high"] = 101.0
    bar["low"] = 100.0

    chart = TechnicalChart([bar])

    assert chart.history[0].high == 101.0
    assert chart.history[0].low == 100.0


def test_technical_chart_rejects_negative_tick_volume() -> None:
    bar = _valid_bar()
    bar["tick_volume"] = -1

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "history[0].tick_volume は0以上の整数で指定してください",
    )


def test_technical_chart_rejects_fractional_spread() -> None:
    bar = _valid_bar()
    bar["spread"] = 1.5

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "history[0].spread は0以上の整数で指定してください",
    )


def test_technical_chart_rejects_boolean_volume() -> None:
    bar = _valid_bar()
    bar["tick_volume"] = False

    _assert_value_error(
        lambda: TechnicalChart([bar]),
        "history[0].tick_volume は数値で指定してください",
    )


def test_calculate_sma_uses_requested_period() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0, 4.0])

    assert chart.moving_average(3) == (None, None, 2.0, 3.0)


def test_calculate_sma_period_one_returns_each_price() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0])

    assert chart.moving_average(1) == (1.0, 2.0, 3.0)


def test_calculate_sma_returns_none_until_period_is_available() -> None:
    chart = _chart_from_closes([1.0, 2.0])

    assert chart.moving_average(3) == (None, None)


def test_calculate_ema_uses_first_price_as_initial_value() -> None:
    chart = _chart_from_closes(
        [1.0, 2.0, 3.0, 4.0],
        moving_average_method="EMA",
    )

    assert chart.moving_average(3) == (1.0, 1.5, 2.25, 3.125)


def test_calculate_smma_uses_smoothed_average() -> None:
    chart = _chart_from_closes(
        [1.0, 2.0, 3.0, 4.0],
        moving_average_method="SMMA",
    )
    result = chart.moving_average(3)

    assert result[:3] == (None, None, 2.0)
    assert result[3] is not None
    assert math.isclose(result[3], 8 / 3)


def test_calculate_smma_returns_none_when_period_is_unavailable() -> None:
    chart = _chart_from_closes(
        [1.0, 2.0],
        moving_average_method="SMMA",
    )

    assert chart.moving_average(3) == (None, None)


def test_calculate_lwma_weights_latest_value_most() -> None:
    chart = _chart_from_closes(
        [1.0, 2.0, 3.0, 4.0],
        moving_average_method="LWMA",
    )
    result = chart.moving_average(3)

    assert result[:2] == (None, None)
    assert result[2] is not None
    assert result[3] is not None
    assert math.isclose(result[2], 14 / 6)
    assert math.isclose(result[3], 20 / 6)


def test_calculate_lwma_returns_none_when_period_is_unavailable() -> None:
    chart = _chart_from_closes(
        [1.0, 2.0],
        moving_average_method="LWMA",
    )

    assert chart.moving_average(3) == (None, None)


def test_calculate_moving_average_accepts_mt5_method_alias() -> None:
    chart = _chart_from_closes(
        [1.0, 2.0, 3.0, 4.0],
        moving_average_method=" MODE_EMA ",
    )

    assert chart.moving_average(3) == (1.0, 1.5, 2.25, 3.125)


def test_calculate_applied_prices_supports_mt5_price_types() -> None:
    expected_values = {
        "CLOSE": 101.0,
        "OPEN": 100.0,
        "HIGH": 102.0,
        "LOW": 99.0,
        "MEDIAN": 100.5,
        "TYPICAL": 302 / 3,
        "WEIGHTED": 403 / 4,
    }

    for applied_price, expected in expected_values.items():
        chart = TechnicalChart([_valid_bar()], applied_price=applied_price)
        assert chart.moving_average(1) == (expected,)


def test_calculate_applied_prices_accepts_mt5_price_alias() -> None:
    chart = TechnicalChart(
        [_valid_bar()],
        applied_price=" price_typical ",
    )

    value = chart.moving_average(1)[0]
    assert value is not None
    assert math.isclose(value, 302 / 3)


def test_normalizers_accept_lowercase_and_mt5_names() -> None:
    assert normalize_moving_average_method(" sma ") == "SMA"
    assert normalize_moving_average_method("mode_lwma") == "LWMA"
    assert normalize_applied_price(" low ") == "LOW"
    assert normalize_applied_price("price_typical") == "TYPICAL"
    assert normalize_applied_price("") == "CLOSE"


def test_normalizers_reject_unknown_values() -> None:
    _assert_value_error(
        lambda: normalize_moving_average_method("WMA"),
        "MA方式は SMA, EMA, SMMA, LWMA から指定してください",
    )
    _assert_value_error(
        lambda: normalize_applied_price("ADJUSTED_CLOSE"),
        "適用価格は CLOSE, OPEN, HIGH, LOW, MEDIAN, TYPICAL, WEIGHTED",
    )


def test_moving_average_rejects_invalid_periods() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0])

    for period in (0, -1, True, 1.5):
        _assert_value_error(
            lambda period=period: chart.moving_average(
                period  # type: ignore[arg-type]
            ),
            "MAの期間は1以上の整数で指定してください",
        )


def test_calculate_rsi_for_rising_prices() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0, 4.0])

    assert chart.rsi(2, bar_shift=0) == 100.0


def test_calculate_rsi_for_falling_prices() -> None:
    chart = _chart_from_closes([4.0, 3.0, 2.0, 1.0])

    assert chart.rsi(2, bar_shift=0) == 0.0


def test_calculate_rsi_for_flat_prices() -> None:
    chart = _chart_from_closes([10.0, 10.0, 10.0, 10.0])

    assert chart.rsi(2, bar_shift=0) == 50.0


def test_calculate_rsi_uses_wilder_smoothing() -> None:
    chart = _chart_from_closes([10.0, 12.0, 11.0, 14.0, 13.0])

    latest = chart.rsi(2, bar_shift=0)
    previous = chart.rsi(2, bar_shift=1)
    initial = chart.rsi(2, bar_shift=2)

    assert latest is not None
    assert previous is not None
    assert initial is not None
    assert math.isclose(latest, 61.53846153846154)
    assert math.isclose(previous, 88.88888888888889)
    assert math.isclose(initial, 66.66666666666666)


def test_calculate_rsi_returns_none_until_period_is_available() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0])

    assert chart.rsi(3, bar_shift=0) is None
    assert chart.rsi(2, bar_shift=1) is None


def test_bar_shift_selects_older_cross_target() -> None:
    chart = _chart_from_closes([10, 10, 10, 10, 20, 100])

    assert chart.is_golden_cross(2, 4, bar_shift=1) is True
    assert chart.is_golden_cross(2, 4, bar_shift=0) is False


def test_cross_returns_none_when_target_or_ma_is_unavailable() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0])

    assert chart.is_golden_cross(2, 3, bar_shift=3) is None
    assert chart.is_death_cross(2, 4, bar_shift=0) is None
    assert chart.cross_status(2, 4, bar_shift=0) == "判定不可（データ不足）"


def test_cross_rejects_invalid_period_order() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0, 4.0])

    _assert_value_error(
        lambda: chart.is_golden_cross(3, 3, bar_shift=0),
        "移動平均の期間は短期 < 比較対象で指定してください",
    )
    _assert_value_error(
        lambda: chart.is_death_cross(4, 2, bar_shift=0),
        "移動平均の期間は短期 < 比較対象で指定してください",
    )


def test_bar_shift_rejects_invalid_values() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0, 4.0])

    for shift in (-1, True, 1.5):
        _assert_value_error(
            lambda shift=shift: chart.rsi(2, shift),  # type: ignore[arg-type]
            "バーシフトは0以上の整数で指定してください",
        )


def test_analyze_detects_golden_crosses() -> None:
    chart = _chart_from_closes([10, 10, 10, 10, 20, 100])
    result = chart.analyze(
        moving_average_periods=(2, 3, 4),
        rsi_period=2,
        bar_shift=1,
    )

    assert result.short_long_cross == "ゴールデンクロス"
    assert result.short_middle_cross == "ゴールデンクロス"
    assert result.rsi == 100.0


def test_analyze_detects_death_crosses() -> None:
    chart = _chart_from_closes([20, 20, 20, 20, 10, 0])
    result = chart.analyze(
        moving_average_periods=(2, 3, 4),
        rsi_period=2,
        bar_shift=1,
    )

    assert result.short_long_cross == "デッドクロス"
    assert result.short_middle_cross == "デッドクロス"
    assert result.rsi == 0.0


def test_analyze_handles_insufficient_data() -> None:
    result = _chart_from_closes([100.0]).analyze()

    assert result.short_long_cross == "判定不可（データ不足）"
    assert result.short_middle_cross == "判定不可（データ不足）"
    assert result.rsi is None


def test_analyze_rejects_invalid_moving_average_periods() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0, 4.0])
    invalid_periods = (
        (1, 2),
        (0, 2, 3),
        (True, 2, 3),
        (1, 2, 2),
        (3, 2, 1),
    )

    for periods in invalid_periods:
        _assert_value_error(
            lambda periods=periods: chart.analyze(  # type: ignore[arg-type]
                moving_average_periods=periods,
            ),
            "移動平均の期間",
        )


def test_analyze_rejects_invalid_rsi_period() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0, 4.0])

    for period in (0, -1, True, 1.5):
        _assert_value_error(
            lambda period=period: chart.analyze(  # type: ignore[arg-type]
                moving_average_periods=(1, 2, 3),
                rsi_period=period,
            ),
            "RSI の期間は1以上の整数で指定してください",
        )
