from datetime import datetime

from services.cross_scan_service import scan_moving_average_crosses


def _history(closes: list[float]) -> list[object]:
    return [
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


def test_scan_detects_short_middle_and_short_long_golden_crosses() -> None:
    result = scan_moving_average_crosses(
        symbol="GOLD",
        period="M30",
        history=_history([10, 10, 10, 10, 20, 100]),
        moving_average_periods=(2, 3, 4),
        end_bar_shift=1,
    )

    assert len(result.crosses) == 2
    assert [cross.cross_type for cross in result.crosses] == [
        "golden_cross",
        "golden_cross",
    ]
    assert [cross.comparison_period for cross in result.crosses] == [3, 4]
    assert all(cross.short_period == 2 for cross in result.crosses)
    assert all(cross.bar_time == datetime(2026, 8, 15, 4) for cross in result.crosses)


def test_scan_detects_short_middle_and_short_long_death_crosses() -> None:
    result = scan_moving_average_crosses(
        symbol="GOLD",
        period="M15",
        history=_history([20, 20, 20, 20, 10, 0]),
        moving_average_periods=(2, 3, 4),
        end_bar_shift=1,
    )

    assert len(result.crosses) == 2
    assert [cross.cross_type for cross in result.crosses] == [
        "death_cross",
        "death_cross",
    ]
    assert [cross.comparison_period for cross in result.crosses] == [3, 4]


def test_scan_end_bar_shift_excludes_requested_trailing_bars() -> None:
    history = _history([10, 10, 10, 10, 10, 20])

    excluded = scan_moving_average_crosses(
        symbol="GOLD",
        period="M5",
        history=history,
        moving_average_periods=(2, 3, 4),
        end_bar_shift=1,
    )
    included = scan_moving_average_crosses(
        symbol="GOLD",
        period="M5",
        history=history,
        moving_average_periods=(2, 3, 4),
        end_bar_shift=0,
    )

    assert excluded.crosses == ()
    assert len(included.crosses) == 2
    assert all(cross.bar_time == datetime(2026, 8, 15, 5) for cross in included.crosses)


def test_scan_filters_output_by_datetime_without_removing_warmup() -> None:
    result = scan_moving_average_crosses(
        symbol="GOLD",
        period="M30",
        history=_history([10, 10, 10, 10, 20, 100]),
        moving_average_periods=(2, 3, 4),
        end_bar_shift=1,
        scan_from=datetime(2026, 8, 15, 4),
        scan_to=datetime(2026, 8, 15, 5),
    )

    assert len(result.crosses) == 2
    assert all(cross.bar_time == datetime(2026, 8, 15, 4) for cross in result.crosses)


def test_scan_returns_dataset_coverage() -> None:
    result = scan_moving_average_crosses(
        symbol=" GOLD ",
        period=" M30 ",
        history=_history([10, 10, 10, 10, 20, 100]),
        moving_average_periods=(2, 3, 4),
    )

    assert result.symbol == "GOLD"
    assert result.period == "M30"
    assert result.first_bar_time == datetime(2026, 8, 15, 0)
    assert result.last_bar_time == datetime(2026, 8, 15, 5)
    assert result.bar_count == 6


def test_scan_rejects_invalid_scan_range() -> None:
    try:
        scan_moving_average_crosses(
            symbol="GOLD",
            period="M30",
            history=_history([10, 10, 10, 10]),
            moving_average_periods=(2, 3, 4),
            scan_from=datetime(2026, 8, 15, 2),
            scan_to=datetime(2026, 8, 15, 2),
        )
    except ValueError as exc:
        assert str(exc) == "走査終了日時は走査開始日時より後に指定してください"
    else:
        raise AssertionError("ValueErrorが発生しませんでした")
