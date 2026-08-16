from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Mapping, Optional, Sequence, Tuple, cast

MOVING_AVERAGE_METHODS = ("SMA", "EMA", "SMMA", "LWMA")
APPLIED_PRICES = (
    "CLOSE",
    "OPEN",
    "HIGH",
    "LOW",
    "MEDIAN",
    "TYPICAL",
    "WEIGHTED",
)


@dataclass(frozen=True)
class MarketBar:
    """MCP の history に含まれる1本のローソク足。"""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int


@dataclass(frozen=True)
class TechnicalAnalysisResult:
    """指定したバーを基準にしたテクニカル分析結果。"""

    short_long_cross: str
    short_middle_cross: str
    rsi: Optional[float]


def normalize_moving_average_method(method: str) -> str:
    """MA方式をMT5の別名を含めて正規化する。"""
    normalized = method.strip().upper()
    if normalized.startswith("MODE_"):
        normalized = normalized[5:]
    if normalized not in MOVING_AVERAGE_METHODS:
        choices = ", ".join(MOVING_AVERAGE_METHODS)
        raise ValueError(f"MA方式は {choices} から指定してください")
    return normalized


def normalize_applied_price(applied_price: str) -> str:
    """適用価格をMT5の別名を含めて正規化する。"""
    normalized = applied_price.strip().upper()
    if not normalized:
        return "CLOSE"
    if normalized.startswith("PRICE_"):
        normalized = normalized[6:]
    if normalized not in APPLIED_PRICES:
        choices = ", ".join(APPLIED_PRICES)
        raise ValueError(f"適用価格は {choices} から指定してください")
    return normalized


class TechnicalChart:
    """チャートデータを保持し、MAクロスとRSIを計算する。"""

    def __init__(
        self,
        history: Sequence[object],
        moving_average_method: str = "SMA",
        applied_price: str = "CLOSE",
    ) -> None:
        self.history = self._parse_history(history)
        self.moving_average_method = normalize_moving_average_method(
            moving_average_method
        )
        self.applied_price = normalize_applied_price(applied_price)
        self._indicator_prices = tuple(
            self._calculate_applied_prices(self.history)
        )
        self._moving_average_cache: dict[
            int,
            Tuple[Optional[float], ...],
        ] = {}
        self._rsi_cache: dict[int, Tuple[Optional[float], ...]] = {}

    def moving_average(
        self,
        period: int,
    ) -> Tuple[Optional[float], ...]:
        """指定期間の移動平均系列を返す。"""
        self._validate_indicator_values(self._indicator_prices, period, "MA")
        if period not in self._moving_average_cache:
            self._moving_average_cache[period] = tuple(
                self._calculate_moving_average(
                    self._indicator_prices,
                    period,
                )
            )
        return self._moving_average_cache[period]

    def is_golden_cross(
        self,
        short_period: int,
        comparison_period: int,
        bar_shift: int = 1,
    ) -> Optional[bool]:
        """短期MAが比較対象MAを上抜けたか判定する。"""
        values = self._moving_average_cross_values(
            short_period,
            comparison_period,
            bar_shift,
        )
        if values is None:
            return None
        (
            previous_short,
            previous_comparison,
            latest_short,
            latest_comparison,
        ) = values
        return (
            previous_short <= previous_comparison
            and latest_short > latest_comparison
        )

    def is_death_cross(
        self,
        short_period: int,
        comparison_period: int,
        bar_shift: int = 1,
    ) -> Optional[bool]:
        """短期MAが比較対象MAを下抜けたか判定する。"""
        values = self._moving_average_cross_values(
            short_period,
            comparison_period,
            bar_shift,
        )
        if values is None:
            return None
        (
            previous_short,
            previous_comparison,
            latest_short,
            latest_comparison,
        ) = values
        return (
            previous_short >= previous_comparison
            and latest_short < latest_comparison
        )

    def cross_status(
        self,
        short_period: int,
        comparison_period: int,
        bar_shift: int = 1,
    ) -> str:
        """2本のMAのクロス状態を返す。"""
        golden_cross = self.is_golden_cross(
            short_period,
            comparison_period,
            bar_shift,
        )
        if golden_cross is None:
            return "判定不可（データ不足）"
        if golden_cross:
            return "ゴールデンクロス"
        if self.is_death_cross(
            short_period,
            comparison_period,
            bar_shift,
        ):
            return "デッドクロス"
        return "クロスなし"

    def rsi(
        self,
        period: int = 14,
        bar_shift: int = 1,
    ) -> Optional[float]:
        """指定したバーのRSIを返す。"""
        self._validate_indicator_values(self._indicator_prices, period, "RSI")
        latest_index = self._target_index(bar_shift)
        if latest_index is None:
            return None
        if period not in self._rsi_cache:
            self._rsi_cache[period] = tuple(
                self._calculate_rsi(self._indicator_prices, period)
            )
        return self._rsi_cache[period][latest_index]

    def analyze(
        self,
        moving_average_periods: Sequence[int] = (5, 20, 60),
        rsi_period: int = 14,
        bar_shift: int = 1,
    ) -> TechnicalAnalysisResult:
        """短期MAと中長期MAのクロス、およびRSIを返す。"""
        short_period, middle_period, long_period = self._validate_analysis_options(
            moving_average_periods,
            rsi_period,
            bar_shift,
        )
        return TechnicalAnalysisResult(
            short_long_cross=self.cross_status(
                short_period,
                long_period,
                bar_shift,
            ),
            short_middle_cross=self.cross_status(
                short_period,
                middle_period,
                bar_shift,
            ),
            rsi=self.rsi(rsi_period, bar_shift),
        )

    def _moving_average_cross_values(
        self,
        short_period: int,
        comparison_period: int,
        bar_shift: int,
    ) -> Optional[Tuple[float, float, float, float]]:
        if short_period >= comparison_period:
            raise ValueError(
                "移動平均の期間は短期 < 比較対象で指定してください"
            )
        latest_index = self._target_index(bar_shift)
        if latest_index is None or latest_index == 0:
            return None
        previous_index = latest_index - 1
        short_values = self.moving_average(short_period)
        comparison_values = self.moving_average(comparison_period)
        values = (
            short_values[previous_index],
            comparison_values[previous_index],
            short_values[latest_index],
            comparison_values[latest_index],
        )
        if any(value is None for value in values):
            return None
        return cast(Tuple[float, float, float, float], values)

    def _target_index(self, bar_shift: int) -> Optional[int]:
        self._validate_bar_shift(bar_shift)
        latest_index = len(self.history) - 1 - bar_shift
        return latest_index if latest_index >= 0 else None

    @classmethod
    def _parse_history(
        cls,
        raw_history: Sequence[object],
    ) -> Tuple[MarketBar, ...]:
        if not raw_history:
            raise ValueError("history は1件以上の配列で指定してください")

        history: List[MarketBar] = []
        for index, item in enumerate(raw_history):
            if not isinstance(item, Mapping):
                raise ValueError(
                    f"history[{index}] はオブジェクトで指定してください"
                )
            row = cast(Mapping[str, object], item)
            time_text = cls._read_text(row, "time", f"history[{index}]")
            try:
                bar_time = datetime.strptime(
                    time_text,
                    "%Y.%m.%d %H:%M:%S",
                )
            except ValueError as exc:
                raise ValueError(
                    f"history[{index}].time は YYYY.MM.DD HH:MM:SS 形式で"
                    "指定してください"
                ) from exc

            open_price = cls._read_number(row, "open", index)
            high_price = cls._read_number(row, "high", index)
            low_price = cls._read_number(row, "low", index)
            close_price = cls._read_number(row, "close", index)
            if low_price > high_price:
                raise ValueError(
                    f"history[{index}] は low が high を超えています"
                )
            if high_price < max(open_price, close_price):
                raise ValueError(
                    f"history[{index}] は high が始値または終値未満です"
                )
            if low_price > min(open_price, close_price):
                raise ValueError(
                    f"history[{index}] は low が始値または終値より大きいです"
                )

            history.append(
                MarketBar(
                    time=bar_time,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    tick_volume=cls._read_non_negative_integer(
                        row,
                        "tick_volume",
                        index,
                    ),
                    spread=cls._read_non_negative_integer(
                        row,
                        "spread",
                        index,
                    ),
                )
            )

        history.sort(key=lambda bar: bar.time)
        return tuple(history)

    def _calculate_moving_average(
        self,
        values: Sequence[float],
        period: int,
    ) -> List[Optional[float]]:
        calculators: Mapping[
            str,
            Callable[[Sequence[float], int], List[Optional[float]]],
        ] = {
            "SMA": self._calculate_sma,
            "EMA": self._calculate_ema,
            "SMMA": self._calculate_smma,
            "LWMA": self._calculate_lwma,
        }
        return calculators[self.moving_average_method](values, period)

    @classmethod
    def _calculate_sma(
        cls,
        values: Sequence[float],
        period: int,
    ) -> List[Optional[float]]:
        cls._validate_indicator_values(values, period, "SMA")
        result: List[Optional[float]] = [None] * len(values)
        total = 0.0
        for index, value in enumerate(values):
            total += value
            if index >= period:
                total -= values[index - period]
            if index >= period - 1:
                result[index] = total / period
        return result

    @classmethod
    def _calculate_ema(
        cls,
        values: Sequence[float],
        period: int,
    ) -> List[Optional[float]]:
        cls._validate_indicator_values(values, period, "EMA")
        result: List[Optional[float]] = [None] * len(values)
        if not values:
            return result

        previous = values[0]
        result[0] = previous
        smoothing = 2.0 / (period + 1)
        for index in range(1, len(values)):
            previous = (
                values[index] * smoothing
                + previous * (1 - smoothing)
            )
            result[index] = previous
        return result

    @classmethod
    def _calculate_smma(
        cls,
        values: Sequence[float],
        period: int,
    ) -> List[Optional[float]]:
        cls._validate_indicator_values(values, period, "SMMA")
        result: List[Optional[float]] = [None] * len(values)
        if len(values) < period:
            return result

        previous = sum(values[:period]) / period
        result[period - 1] = previous
        for index in range(period, len(values)):
            previous = ((previous * (period - 1)) + values[index]) / period
            result[index] = previous
        return result

    @classmethod
    def _calculate_lwma(
        cls,
        values: Sequence[float],
        period: int,
    ) -> List[Optional[float]]:
        cls._validate_indicator_values(values, period, "LWMA")
        result: List[Optional[float]] = [None] * len(values)
        if len(values) < period:
            return result

        denominator = period * (period + 1) / 2
        simple_sum = sum(values[:period])
        weighted_sum = sum(
            value * weight
            for weight, value in enumerate(values[:period], start=1)
        )
        result[period - 1] = weighted_sum / denominator
        for index in range(period, len(values)):
            weighted_sum = (
                weighted_sum - simple_sum + values[index] * period
            )
            simple_sum += values[index] - values[index - period]
            result[index] = weighted_sum / denominator
        return result

    def _calculate_applied_prices(
        self,
        history: Sequence[MarketBar],
    ) -> List[float]:
        result: List[float] = []
        for bar in history:
            if self.applied_price == "CLOSE":
                value = bar.close
            elif self.applied_price == "OPEN":
                value = bar.open
            elif self.applied_price == "HIGH":
                value = bar.high
            elif self.applied_price == "LOW":
                value = bar.low
            elif self.applied_price == "MEDIAN":
                value = (bar.high + bar.low) / 2
            elif self.applied_price == "TYPICAL":
                value = (bar.high + bar.low + bar.close) / 3
            else:
                value = (bar.high + bar.low + bar.close + bar.close) / 4
            result.append(value)
        return result

    @classmethod
    def _calculate_rsi(
        cls,
        values: Sequence[float],
        period: int,
    ) -> List[Optional[float]]:
        cls._validate_indicator_values(values, period, "RSI")
        result: List[Optional[float]] = [None] * len(values)
        if len(values) <= period:
            return result

        gains: List[float] = []
        losses: List[float] = []
        for index in range(1, period + 1):
            difference = values[index] - values[index - 1]
            gains.append(max(difference, 0.0))
            losses.append(max(-difference, 0.0))

        average_gain = sum(gains) / period
        average_loss = sum(losses) / period
        result[period] = cls._rsi_value(average_gain, average_loss)

        for index in range(period + 1, len(values)):
            difference = values[index] - values[index - 1]
            gain = max(difference, 0.0)
            loss = max(-difference, 0.0)
            average_gain = ((average_gain * (period - 1)) + gain) / period
            average_loss = ((average_loss * (period - 1)) + loss) / period
            result[index] = cls._rsi_value(average_gain, average_loss)
        return result

    @staticmethod
    def _rsi_value(average_gain: float, average_loss: float) -> float:
        if average_gain == 0 and average_loss == 0:
            return 50.0
        if average_loss == 0:
            return 100.0
        relative_strength = average_gain / average_loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    @staticmethod
    def _read_text(
        data: Mapping[str, object],
        key: str,
        prefix: str = "",
    ) -> str:
        value = data.get(key)
        label = f"{prefix}.{key}" if prefix else key
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} は空でない文字列で指定してください")
        return value.strip()

    @staticmethod
    def _read_number(
        data: Mapping[str, object],
        key: str,
        index: int,
    ) -> float:
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"history[{index}].{key} は数値で指定してください"
            )
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(
                f"history[{index}].{key} は有限の数値で指定してください"
            )
        return number

    @classmethod
    def _read_non_negative_integer(
        cls,
        data: Mapping[str, object],
        key: str,
        index: int,
    ) -> int:
        number = cls._read_number(data, key, index)
        if number < 0 or not number.is_integer():
            raise ValueError(
                f"history[{index}].{key} は0以上の整数で指定してください"
            )
        return int(number)

    @staticmethod
    def _validate_indicator_values(
        values: Sequence[float],
        period: int,
        indicator_name: str,
    ) -> None:
        if (
            isinstance(period, bool)
            or not isinstance(period, int)
            or period <= 0
        ):
            raise ValueError(
                f"{indicator_name}の期間は1以上の整数で指定してください"
            )
        if any(not math.isfinite(value) for value in values):
            raise ValueError(
                f"{indicator_name}の入力値は有限の数値で指定してください"
            )

    @classmethod
    def _validate_analysis_options(
        cls,
        moving_average_periods: Sequence[int],
        rsi_period: int,
        bar_shift: int,
    ) -> Tuple[int, int, int]:
        if len(moving_average_periods) != 3:
            raise ValueError(
                "移動平均の期間は短期,中期,長期の3つを指定してください"
            )
        if any(
            isinstance(period, bool)
            or not isinstance(period, int)
            or period <= 0
            for period in moving_average_periods
        ):
            raise ValueError(
                "移動平均の期間は1以上の整数で指定してください"
            )
        if len(set(moving_average_periods)) != len(moving_average_periods):
            raise ValueError(
                "移動平均の期間は重複しないように指定してください"
            )
        short_period, middle_period, long_period = moving_average_periods
        if not short_period < middle_period < long_period:
            raise ValueError(
                "移動平均の期間は短期 < 中期 < 長期で指定してください"
            )
        if (
            isinstance(rsi_period, bool)
            or not isinstance(rsi_period, int)
            or rsi_period <= 0
        ):
            raise ValueError("RSI の期間は1以上の整数で指定してください")
        cls._validate_bar_shift(bar_shift)
        return short_period, middle_period, long_period

    @staticmethod
    def _validate_bar_shift(bar_shift: int) -> None:
        if (
            isinstance(bar_shift, bool)
            or not isinstance(bar_shift, int)
            or bar_shift < 0
        ):
            raise ValueError("バーシフトは0以上の整数で指定してください")
