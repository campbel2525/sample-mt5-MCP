from services.chart_service import TechnicalChart


def _chart(closes: list[float]) -> TechnicalChart:
    return TechnicalChart(
        [
            {
                "time": f"2026.08.15 {index:02d}:00:00",
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "tick_volume": 100,
                "spread": 20,
            }
            for index, close in enumerate(closes)
        ]
    )


def test_is_golden_cross_basic_true() -> None:
    assert _chart([12, 10, 10, 14]).is_golden_cross(2, 3, bar_shift=0)


def test_is_golden_cross_equal_then_cross() -> None:
    assert _chart([10, 10, 10, 12]).is_golden_cross(2, 3, bar_shift=0)


def test_is_golden_cross_no_cross_latest_not_above() -> None:
    assert not _chart([12, 10, 10, 10]).is_golden_cross(
        2,
        3,
        bar_shift=0,
    )


def test_is_golden_cross_no_cross_prev_above() -> None:
    assert not _chart([10, 11, 12, 13]).is_golden_cross(
        2,
        3,
        bar_shift=0,
    )
