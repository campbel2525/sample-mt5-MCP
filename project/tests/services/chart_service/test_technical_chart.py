import math

from services.chart_service import (
    TechnicalChart,
    normalize_applied_price,
    normalize_moving_average_method,
)


def _payload(bar_count: int = 20) -> dict[str, object]:
    history = []
    for index in range(bar_count):
        price = 100.0 + index
        history.append(
            {
                "time": f"2026.08.15 {index:02d}:00:00",
                "open": price,
                "high": price + 2.0,
                "low": price - 1.0,
                "close": price + 1.0,
                "tick_volume": 100 + index,
                "spread": 20,
            }
        )
    return {
        "symbol": "BTCUSD",
        "period": "H1",
        "data_available_from": "2013.01.01 00:00:00",
        "history": history,
    }


def _payload_from_closes(closes: list[float]) -> dict[str, object]:
    payload = _payload(len(closes))
    history = payload["history"]
    assert isinstance(history, list)
    for bar, close in zip(history, closes):
        bar["open"] = close
        bar["high"] = close + 1
        bar["low"] = close - 1
        bar["close"] = close
    return payload


def _history(payload: dict[str, object]) -> list[object]:
    history = payload["history"]
    assert isinstance(history, list)
    return history


def _chart_from_closes(
    closes: list[float],
    moving_average_method: str = "SMA",
    applied_price: str = "CLOSE",
) -> TechnicalChart:
    return TechnicalChart(
        _history(_payload_from_closes(closes)),
        moving_average_method=moving_average_method,
        applied_price=applied_price,
    )


def test_parse_market_chart_data_sorts_history() -> None:
    payload = _payload(2)
    history = payload["history"]
    assert isinstance(history, list)
    history.reverse()

    chart = TechnicalChart(history)

    assert chart.history[0].time.hour == 0
    assert chart.history[1].time.hour == 1


def test_calculate_sma_uses_requested_period() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0, 4.0])

    assert chart.moving_average(3) == (None, None, 2.0, 3.0)


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


def test_calculate_moving_average_accepts_mt5_method_alias() -> None:
    chart = _chart_from_closes(
        [1.0, 2.0, 3.0, 4.0],
        moving_average_method="MODE_EMA",
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
        chart = TechnicalChart(
            _history(_payload(1)),
            applied_price=applied_price,
        )
        assert chart.moving_average(1) == (expected,)


def test_normalizers_accept_mt5_names() -> None:
    assert normalize_moving_average_method("mode_lwma") == "LWMA"
    assert normalize_applied_price("price_typical") == "TYPICAL"
    assert normalize_applied_price("") == "CLOSE"


def test_calculate_rsi_for_rising_prices() -> None:
    chart = _chart_from_closes([1.0, 2.0, 3.0, 4.0])

    assert chart.rsi(2, bar_shift=0) == 100.0


def test_technical_chart_accepts_history() -> None:
    chart = TechnicalChart(
        _history(_payload_from_closes([10, 10, 10, 10, 20, 100])),
        moving_average_method="MODE_SMA",
        applied_price="PRICE_CLOSE",
    )

    assert chart.is_golden_cross(2, 4, bar_shift=1) is True
    assert chart.is_death_cross(2, 4, bar_shift=1) is False
    assert chart.cross_status(2, 3, bar_shift=1) == "ゴールデンクロス"
    assert chart.rsi(2, bar_shift=1) == 100.0


def test_technical_chart_stores_parsed_history() -> None:
    chart = TechnicalChart(
        _history(_payload_from_closes([20, 20, 20, 20, 10, 0]))
    )

    assert len(chart.history) == 6
    assert chart.is_golden_cross(2, 4, bar_shift=1) is False
    assert chart.is_death_cross(2, 4, bar_shift=1) is True


def test_technical_chart_analyze_detects_golden_crosses() -> None:
    chart = _chart_from_closes([10, 10, 10, 10, 20, 100])
    result = chart.analyze(
        moving_average_periods=(2, 3, 4),
        rsi_period=2,
        bar_shift=1,
    )

    assert result.short_long_cross == "ゴールデンクロス"
    assert result.short_middle_cross == "ゴールデンクロス"
    assert result.rsi == 100.0


def test_technical_chart_analyze_detects_death_crosses() -> None:
    chart = _chart_from_closes([20, 20, 20, 20, 10, 0])
    result = chart.analyze(
        moving_average_periods=(2, 3, 4),
        rsi_period=2,
        bar_shift=1,
    )

    assert result.short_long_cross == "デッドクロス"
    assert result.short_middle_cross == "デッドクロス"
    assert result.rsi == 0.0


def test_technical_chart_analyze_handles_insufficient_data() -> None:
    result = TechnicalChart(_history(_payload(1))).analyze()

    assert result.short_long_cross == "判定不可（データ不足）"
    assert result.short_middle_cross == "判定不可（データ不足）"
    assert result.rsi is None


def test_parse_market_chart_data_rejects_invalid_ohlc() -> None:
    payload = _payload(1)
    history = payload["history"]
    assert isinstance(history, list)
    history[0]["high"] = 100.5

    try:
        TechnicalChart(history)
    except ValueError as exc:
        assert "high が始値または終値未満" in str(exc)
    else:
        raise AssertionError("invalid OHLC was accepted")
