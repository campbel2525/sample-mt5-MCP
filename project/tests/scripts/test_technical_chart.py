import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Optional
from unittest.mock import patch

from scripts.technical_chart import format_technical_analysis, main
from services.chart_service import TechnicalAnalysisResult


def _payload(closes: Optional[list[float]] = None) -> dict[str, object]:
    prices = closes or [10.0, 10.0, 10.0, 10.0, 20.0, 100.0]
    return {
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
            for index, close in enumerate(prices)
        ],
    }


def _arguments(
    payload: object,
    bar_shift: str = "1",
) -> list[str]:
    return [
        "--ma-periods",
        "2,3,4",
        "--ma-method",
        "MODE_SMA",
        "--applied-price",
        "PRICE_CLOSE",
        "--rsi-period",
        "2",
        "--bar-shift",
        bar_shift,
        "--market-json",
        json.dumps(payload),
    ]


def _run_main(
    argv: list[str],
) -> tuple[Optional[int], Optional[int], str, str]:
    stdout = StringIO()
    stderr = StringIO()
    result: Optional[int] = None
    exit_code: Optional[int] = None
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            result = main(argv)
        except SystemExit as exc:
            assert isinstance(exc.code, int)
            exit_code = exc.code
    return result, exit_code, stdout.getvalue(), stderr.getvalue()


def test_format_technical_analysis_outputs_metadata_and_indicators() -> None:
    result = TechnicalAnalysisResult(
        short_long_cross="ゴールデンクロス",
        short_middle_cross="クロスなし",
        rsi=63.42,
    )

    assert format_technical_analysis("BTCUSD", "H1", result) == (
        "- 銘柄: BTCUSD\n"
        "- 期間: H1\n"
        "- 短期移動平均線と長期移動平均線: ゴールデンクロス\n"
        "- 短期移動平均線と中期移動平均線: クロスなし\n"
        "- RSI: 63.42"
    )


def test_format_technical_analysis_handles_unavailable_rsi() -> None:
    result = TechnicalAnalysisResult(
        short_long_cross="判定不可（データ不足）",
        short_middle_cross="判定不可（データ不足）",
        rsi=None,
    )

    output = format_technical_analysis("BTCUSD", "H1", result)

    assert output.endswith("- RSI: 算出不可（データ不足）")


def test_main_outputs_technical_analysis() -> None:
    result, exit_code, stdout, stderr = _run_main(_arguments(_payload()))

    assert result == 0
    assert exit_code is None
    assert stderr == ""
    assert stdout == (
        "- 銘柄: BTCUSD\n"
        "- 期間: H1\n"
        "- 短期移動平均線と長期移動平均線: ゴールデンクロス\n"
        "- 短期移動平均線と中期移動平均線: ゴールデンクロス\n"
        "- RSI: 100.00\n"
    )


def test_main_passes_only_history_and_normalized_options_to_chart() -> None:
    payload = _payload()
    expected_result = TechnicalAnalysisResult(
        short_long_cross="クロスなし",
        short_middle_cross="クロスなし",
        rsi=50.0,
    )
    argv = [
        "--ma-periods",
        "2,3,4",
        "--ma-method",
        "MODE_EMA",
        "--applied-price",
        "PRICE_TYPICAL",
        "--rsi-period",
        "3",
        "--bar-shift",
        "2",
        "--market-json",
        json.dumps(payload),
    ]

    with patch("scripts.technical_chart.TechnicalChart") as chart_class:
        chart = chart_class.return_value
        chart.analyze.return_value = expected_result
        result, exit_code, _, stderr = _run_main(argv)

    assert result == 0
    assert exit_code is None
    assert stderr == ""
    chart_class.assert_called_once_with(
        history=payload["history"],
        moving_average_method="EMA",
        applied_price="TYPICAL",
    )
    chart.analyze.assert_called_once_with(
        moving_average_periods=(2, 3, 4),
        rsi_period=3,
        bar_shift=2,
    )


def test_main_uses_placeholder_for_missing_metadata() -> None:
    payload = _payload()
    del payload["symbol"]
    del payload["period"]

    result, exit_code, stdout, stderr = _run_main(_arguments(payload))

    assert result == 0
    assert exit_code is None
    assert stderr == ""
    assert stdout.startswith("- 銘柄: -\n- 期間: -\n")


def test_main_bar_shift_changes_cross_target() -> None:
    payload = _payload([10, 10, 10, 10, 20, 100])

    _, shift_one_exit, shift_one_output, _ = _run_main(
        _arguments(payload, bar_shift="1")
    )
    _, shift_zero_exit, shift_zero_output, _ = _run_main(
        _arguments(payload, bar_shift="0")
    )

    assert shift_one_exit is None
    assert shift_zero_exit is None
    assert "ゴールデンクロス" in shift_one_output
    assert "ゴールデンクロス" not in shift_zero_output
    assert "クロスなし" in shift_zero_output


def test_main_requires_named_market_json_argument() -> None:
    result, exit_code, stdout, stderr = _run_main([])

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "--market-json" in stderr
    assert "required" in stderr


def test_main_rejects_json_as_unnamed_positional_argument() -> None:
    result, exit_code, stdout, stderr = _run_main(
        [json.dumps(_payload())]
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "--market-json" in stderr


def test_main_rejects_invalid_json() -> None:
    result, exit_code, stdout, stderr = _run_main(
        ["--market-json", "{invalid"],
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "入力値は正しいJSON形式で指定してください" in stderr


def test_main_rejects_non_object_json_root() -> None:
    for payload in ([], "text", 1, None):
        result, exit_code, stdout, stderr = _run_main(
            ["--market-json", json.dumps(payload)],
        )
        assert result is None
        assert exit_code == 2
        assert stdout == ""
        assert "最上位はオブジェクトで指定してください" in stderr


def test_main_rejects_missing_history() -> None:
    result, exit_code, stdout, stderr = _run_main(
        ["--market-json", json.dumps({"symbol": "BTCUSD"})],
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "history は配列で指定してください" in stderr


def test_main_rejects_non_array_history() -> None:
    payload = _payload()
    payload["history"] = {"close": 100}

    result, exit_code, stdout, stderr = _run_main(
        ["--market-json", json.dumps(payload)],
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "history は配列で指定してください" in stderr


def test_main_rejects_empty_history() -> None:
    payload = _payload()
    payload["history"] = []

    result, exit_code, stdout, stderr = _run_main(
        ["--market-json", json.dumps(payload)],
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "history は1件以上の配列で指定してください" in stderr


def test_main_rejects_invalid_history_item() -> None:
    payload = _payload()
    payload["history"] = ["invalid"]

    result, exit_code, stdout, stderr = _run_main(
        ["--market-json", json.dumps(payload)],
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "history[0] はオブジェクトで指定してください" in stderr


def test_main_rejects_invalid_ma_period_syntax() -> None:
    invalid_values = ("a,2,3", "1,2", "1,2,3,4", "0,2,3")

    for value in invalid_values:
        argv = [
            "--ma-periods",
            value,
            "--market-json",
            json.dumps(_payload()),
        ]
        result, exit_code, stdout, stderr = _run_main(argv)
        assert result is None
        assert exit_code == 2
        assert stdout == ""
        assert "移動平均の期間" in stderr


def test_main_rejects_duplicate_or_unsorted_ma_periods() -> None:
    invalid_values = ("1,2,2", "3,2,1", "2,1,3")

    for value in invalid_values:
        argv = [
            "--ma-periods",
            value,
            "--market-json",
            json.dumps(_payload()),
        ]
        result, exit_code, stdout, stderr = _run_main(argv)
        assert result is None
        assert exit_code == 2
        assert stdout == ""
        assert "移動平均の期間" in stderr


def test_main_rejects_unknown_ma_method() -> None:
    argv = [
        "--ma-method",
        "WMA",
        "--market-json",
        json.dumps(_payload()),
    ]

    result, exit_code, stdout, stderr = _run_main(argv)

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "MA方式は SMA, EMA, SMMA, LWMA" in stderr


def test_main_rejects_unknown_applied_price() -> None:
    argv = [
        "--applied-price",
        "ADJUSTED_CLOSE",
        "--market-json",
        json.dumps(_payload()),
    ]

    result, exit_code, stdout, stderr = _run_main(argv)

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "適用価格は CLOSE, OPEN, HIGH, LOW" in stderr


def test_main_rejects_invalid_rsi_period() -> None:
    for value in ("0", "-1", "invalid"):
        argv = [
            "--rsi-period",
            value,
            "--market-json",
            json.dumps(_payload()),
        ]
        result, exit_code, stdout, stderr = _run_main(argv)
        assert result is None
        assert exit_code == 2
        assert stdout == ""
        assert "RSI" in stderr or "invalid int value" in stderr


def test_main_rejects_invalid_bar_shift() -> None:
    for value in ("-1", "invalid"):
        argv = [
            "--bar-shift",
            value,
            "--market-json",
            json.dumps(_payload()),
        ]
        result, exit_code, stdout, stderr = _run_main(argv)
        assert result is None
        assert exit_code == 2
        assert stdout == ""
        assert "バーシフト" in stderr or "invalid int value" in stderr
