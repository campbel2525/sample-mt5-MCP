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


def test_is_death_cross_basic_true() -> None:
    assert _chart([10, 12, 12, 8]).is_death_cross(2, 3, bar_shift=0)


def test_is_death_cross_equal_then_cross() -> None:
    assert _chart([10, 10, 10, 8]).is_death_cross(2, 3, bar_shift=0)


def test_is_death_cross_no_cross_latest_not_below() -> None:
    assert not _chart([10, 12, 12, 12]).is_death_cross(
        2,
        3,
        bar_shift=0,
    )


def test_is_death_cross_no_cross_prev_below() -> None:
    assert not _chart([12, 11, 10, 9]).is_death_cross(
        2,
        3,
        bar_shift=0,
    )
