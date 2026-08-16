import json
from contextlib import redirect_stdout
from io import StringIO

from scripts.technical_chart import format_technical_analysis, main
from services.chart_service import TechnicalAnalysisResult


def test_format_technical_analysis_outputs_three_bullets() -> None:
    result = TechnicalAnalysisResult(
        short_long_cross="ゴールデンクロス",
        short_middle_cross="クロスなし",
        rsi=63.42,
    )

    assert format_technical_analysis(result) == (
        "- 短期移動平均線と長期移動平均線: ゴールデンクロス\n"
        "- 短期移動平均線と中期移動平均線: クロスなし\n"
        "- RSI: 63.42"
    )


def test_main_outputs_technical_analysis() -> None:
    closes = [10.0, 10.0, 10.0, 10.0, 20.0, 100.0]
    payload = {
        "symbol": "BTCUSD",
        "period": "H1",
        "data_available_from": "2013.01.01 00:00:00",
        "history": [
            {
                "time": f"2026.08.15 {index:02d}:00:00",
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "tick_volume": 100,
                "spread": 4000,
            }
            for index, close in enumerate(closes)
        ],
    }
    output = StringIO()
    with redirect_stdout(output):
        result = main(
            [
                "--ma-periods",
                "2,3,4",
                "--ma-method",
                "MODE_SMA",
                "--applied-price",
                "PRICE_CLOSE",
                "--rsi-period",
                "2",
                "--bar-shift",
                "1",
                "--market-json",
                json.dumps(payload),
            ]
        )

    assert result == 0
    assert output.getvalue() == (
        "- 短期移動平均線と長期移動平均線: ゴールデンクロス\n"
        "- 短期移動平均線と中期移動平均線: ゴールデンクロス\n"
        "- RSI: 100.00\n"
    )
