"""MCP の OHLCV JSON からMAクロスとRSIを出力する。"""

from __future__ import annotations

import argparse
import json
from typing import List, Optional, Sequence

from services.chart_service import (
    TechnicalAnalysisResult,
    TechnicalChart,
    normalize_applied_price,
    normalize_moving_average_method,
)


def _parse_periods(value: str) -> Sequence[int]:
    try:
        periods = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "移動平均の期間はカンマ区切りの整数で"
            "指定してください"
        ) from exc
    if len(periods) != 3 or any(period <= 0 for period in periods):
        raise argparse.ArgumentTypeError(
            "移動平均の期間は短期,中期,長期の3つを指定してください"
        )
    if len(set(periods)) != len(periods):
        raise argparse.ArgumentTypeError(
            "移動平均の期間は重複できません"
        )
    if not periods[0] < periods[1] < periods[2]:
        raise argparse.ArgumentTypeError(
            "移動平均の期間は短期 < 中期 < 長期で指定してください"
        )
    return periods


def _parse_ma_method(value: str) -> str:
    try:
        return normalize_moving_average_method(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parse_applied_price(value: str) -> str:
    try:
        return normalize_applied_price(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "MCP の JSON から移動平均線のクロスとRSIを箇条書きで出力します。"
        )
    )
    parser.add_argument(
        "--ma-periods",
        type=_parse_periods,
        default=(5, 20, 60),
        metavar="PERIODS",
        help="移動平均の期間（既定: 5,20,60）",
    )
    parser.add_argument(
        "--ma-method",
        type=_parse_ma_method,
        default="SMA",
        metavar="METHOD",
        help="移動平均方式: SMA/EMA/SMMA/LWMA（既定: SMA）",
    )
    parser.add_argument(
        "--applied-price",
        type=_parse_applied_price,
        default="CLOSE",
        metavar="PRICE",
        help=(
            "MA・RSIの適用価格: CLOSE/OPEN/HIGH/LOW/"
            "MEDIAN/TYPICAL/WEIGHTED（既定: CLOSE）"
        ),
    )
    parser.add_argument(
        "--rsi-period",
        type=int,
        default=14,
        metavar="PERIOD",
        help="RSI の期間（既定: 14）",
    )
    parser.add_argument(
        "--bar-shift",
        type=int,
        default=1,
        metavar="SHIFT",
        help="判定対象を最新から何本戻すか。0は最新、1は1本前（既定: 1）",
    )
    parser.add_argument(
        "--market-json",
        required=True,
        metavar="JSON",
        help="MCPから取得したJSON文字列",
    )
    return parser


def format_technical_analysis(
    symbol: str,
    period: str,
    result: TechnicalAnalysisResult,
) -> str:
    """テクニカル分析結果を3行の箇条書きに整形する。"""
    rsi_text = (
        f"{result.rsi:.2f}"
        if result.rsi is not None
        else "算出不可（データ不足）"
    )
    return "\n".join(
        (
            f"- 銘柄: {symbol}",
            f"- 期間: {period}",
            "- 短期移動平均線と長期移動平均線: "
            f"{result.short_long_cross}",
            "- 短期移動平均線と中期移動平均線: "
            f"{result.short_middle_cross}",
            f"- RSI: {rsi_text}",
        )
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        try:
            market_data = json.loads(args.market_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "入力値は正しいJSON形式で指定してください"
            ) from exc
        if not isinstance(market_data, dict):
            raise ValueError(
                "入力 JSON の最上位はオブジェクトで指定してください"
            )
        history = market_data.get("history")
        if not isinstance(history, list):
            raise ValueError("history は配列で指定してください")

        chart = TechnicalChart(
            history=history,
            moving_average_method=args.ma_method,
            applied_price=args.applied_price,
        )
        result = chart.analyze(
            moving_average_periods=args.ma_periods,
            rsi_period=args.rsi_period,
            bar_shift=args.bar_shift,
        )
    except (UnicodeError, ValueError) as exc:
        parser.error(str(exc))

    print(format_technical_analysis(
        symbol=market_data.get("symbol", "-"),
        period=market_data.get("period", "-"),
        result=result,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
