from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, Sequence, Tuple, cast

from services.chart_service import TechnicalChart


CrossType = Literal["golden_cross", "death_cross"]


@dataclass(frozen=True)
class MovingAverageCross:
    """全期間走査で検出した1件の移動平均クロス。"""

    symbol: str
    period: str
    bar_time: datetime
    cross_type: CrossType
    short_period: int
    comparison_period: int
    previous_short_ma: float
    previous_comparison_ma: float
    current_short_ma: float
    current_comparison_ma: float
    close: float


@dataclass(frozen=True)
class CrossScanResult:
    """1つの時間足に対する走査結果。"""

    symbol: str
    period: str
    first_bar_time: datetime
    last_bar_time: datetime
    bar_count: int
    crosses: Tuple[MovingAverageCross, ...]


def scan_moving_average_crosses(
    symbol: str,
    period: str,
    history: Sequence[object],
    moving_average_periods: Sequence[int] = (5, 20, 60),
    moving_average_method: str = "SMA",
    applied_price: str = "CLOSE",
    end_bar_shift: int = 1,
    scan_from: Optional[datetime] = None,
    scan_to: Optional[datetime] = None,
) -> CrossScanResult:
    """1時間足分の全履歴から短期対中長期のクロスを抽出する。"""
    short_period, middle_period, long_period = _validate_options(
        moving_average_periods,
        end_bar_shift,
        scan_from,
        scan_to,
    )
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol は空でない文字列で指定してください")
    if not isinstance(period, str) or not period.strip():
        raise ValueError("period は空でない文字列で指定してください")

    chart = TechnicalChart(
        history=history,
        moving_average_method=moving_average_method,
        applied_price=applied_price,
    )
    moving_averages = {
        ma_period: chart.moving_average(ma_period)
        for ma_period in (short_period, middle_period, long_period)
    }
    comparison_periods = (middle_period, long_period)
    crosses = []
    last_target_index = len(chart.history) - 1 - end_bar_shift

    for target_index in range(1, last_target_index + 1):
        target_bar = chart.history[target_index]
        if scan_from is not None and target_bar.time < scan_from:
            continue
        if scan_to is not None and target_bar.time >= scan_to:
            continue

        previous_index = target_index - 1
        for comparison_period in comparison_periods:
            values = (
                moving_averages[short_period][previous_index],
                moving_averages[comparison_period][previous_index],
                moving_averages[short_period][target_index],
                moving_averages[comparison_period][target_index],
            )
            if any(value is None for value in values):
                continue
            (
                previous_short_ma,
                previous_comparison_ma,
                current_short_ma,
                current_comparison_ma,
            ) = cast(Tuple[float, float, float, float], values)

            cross_type: Optional[CrossType] = None
            if (
                previous_short_ma <= previous_comparison_ma
                and current_short_ma > current_comparison_ma
            ):
                cross_type = "golden_cross"
            elif (
                previous_short_ma >= previous_comparison_ma
                and current_short_ma < current_comparison_ma
            ):
                cross_type = "death_cross"
            if cross_type is None:
                continue

            crosses.append(
                MovingAverageCross(
                    symbol=symbol.strip(),
                    period=period.strip(),
                    bar_time=target_bar.time,
                    cross_type=cross_type,
                    short_period=short_period,
                    comparison_period=comparison_period,
                    previous_short_ma=previous_short_ma,
                    previous_comparison_ma=previous_comparison_ma,
                    current_short_ma=current_short_ma,
                    current_comparison_ma=current_comparison_ma,
                    close=target_bar.close,
                )
            )

    return CrossScanResult(
        symbol=symbol.strip(),
        period=period.strip(),
        first_bar_time=chart.history[0].time,
        last_bar_time=chart.history[-1].time,
        bar_count=len(chart.history),
        crosses=tuple(crosses),
    )


def _validate_options(
    moving_average_periods: Sequence[int],
    end_bar_shift: int,
    scan_from: Optional[datetime],
    scan_to: Optional[datetime],
) -> Tuple[int, int, int]:
    if len(moving_average_periods) != 3:
        raise ValueError("移動平均の期間は短期,中期,長期の3つを指定してください")
    if any(
        isinstance(ma_period, bool) or not isinstance(ma_period, int) or ma_period <= 0
        for ma_period in moving_average_periods
    ):
        raise ValueError("移動平均の期間は1以上の整数で指定してください")
    if len(set(moving_average_periods)) != len(moving_average_periods):
        raise ValueError("移動平均の期間は重複しないように指定してください")

    short_period, middle_period, long_period = moving_average_periods
    if not short_period < middle_period < long_period:
        raise ValueError("移動平均の期間は短期 < 中期 < 長期で指定してください")
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
    return short_period, middle_period, long_period
