import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from scripts.find_ma_crosses import main


def _dataset(period: str, closes: list[float]) -> dict[str, object]:
    return {
        "symbol": "GOLD",
        "period": period,
        "history": [
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
        ],
    }


def _run_main(
    argv: list[str],
    stdin_text: str = "",
) -> tuple[Optional[int], Optional[int], str, str]:
    stdout = StringIO()
    stderr = StringIO()
    result: Optional[int] = None
    exit_code: Optional[int] = None
    with (
        redirect_stdout(stdout),
        redirect_stderr(stderr),
        patch("scripts.find_ma_crosses.sys.stdin", StringIO(stdin_text)),
    ):
        try:
            result = main(argv)
        except SystemExit as exc:
            assert isinstance(exc.code, int)
            exit_code = exc.code
    return result, exit_code, stdout.getvalue(), stderr.getvalue()


def _write_payload(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_main_scans_multiple_timeframes_and_outputs_json(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "market_history.json"
    _write_payload(
        input_path,
        {
            "datasets": [
                _dataset("M5", [10, 10, 10, 10, 20, 100]),
                _dataset("M30", [10, 10, 10, 10, 20, 100]),
            ]
        },
    )

    result, exit_code, stdout, stderr = _run_main(
        [
            "--input",
            str(input_path),
            "--ma-periods",
            "2,3,4",
            "--end-bar-shift",
            "1",
        ]
    )

    assert result == 0
    assert exit_code is None
    assert stderr == ""
    output = json.loads(stdout)
    assert output["settings"] == {
        "moving_average_method": "SMA",
        "applied_price": "CLOSE",
        "periods": [2, 3, 4],
        "end_bar_shift": 1,
        "scan_from": None,
        "scan_to": None,
    }
    assert [dataset["period"] for dataset in output["datasets"]] == [
        "M5",
        "M30",
    ]
    assert len(output["crosses"]) == 4
    assert {
        (cross["period"], cross["comparison_period"]) for cross in output["crosses"]
    } == {("M5", 3), ("M5", 4), ("M30", 3), ("M30", 4)}


def test_main_writes_output_file(tmp_path: Path) -> None:
    input_path = tmp_path / "market_history.json"
    output_path = tmp_path / "crosses.json"
    _write_payload(
        input_path,
        {"datasets": [_dataset("M30", [10, 10, 10, 10, 20, 100])]},
    )

    result, exit_code, stdout, stderr = _run_main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--ma-periods",
            "2,3,4",
        ]
    )

    assert result == 0
    assert exit_code is None
    assert stdout == ""
    assert stderr == ""
    assert len(json.loads(output_path.read_text(encoding="utf-8"))["crosses"]) == 2


def test_main_saves_input_and_output_in_run_directory(tmp_path: Path) -> None:
    input_path = tmp_path / "source.json"
    run_directory = (
        tmp_path
        / "data"
        / "タスク"
        / "2_Gold売買検証"
        / "1_Goldの売買タイミング"
        / "202608260202"
    )
    payload = {"datasets": [_dataset("M30", [10, 10, 10, 10, 20, 100])]}
    _write_payload(input_path, payload)

    result, exit_code, stdout, stderr = _run_main(
        [
            "--input",
            str(input_path),
            "--run-directory",
            str(run_directory),
            "--ma-periods",
            "2,3,4",
        ]
    )

    assert result == 0
    assert exit_code is None
    assert stdout == ""
    assert stderr == ""
    assert (
        json.loads((run_directory / "market_history.json").read_text(encoding="utf-8"))
        == payload
    )
    assert (
        len(
            json.loads((run_directory / "ma_crosses.json").read_text(encoding="utf-8"))[
                "crosses"
            ]
        )
        == 2
    )


def test_main_uses_gold_task_directory_for_automatic_run_directory(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "source.json"
    root = tmp_path / "data" / "タスク" / "2_Gold売買検証" / "1_Goldの売買タイミング"
    payload = {"datasets": [_dataset("M30", [10, 10, 10, 10, 20, 100])]}
    _write_payload(input_path, payload)

    with patch("scripts.find_ma_crosses.DEFAULT_RUN_DIRECTORY_ROOT", root):
        result, exit_code, stdout, stderr = _run_main(
            [
                "--input",
                str(input_path),
                "--run-directory",
                "--ma-periods",
                "2,3,4",
            ]
        )

    assert result == 0
    assert exit_code is None
    assert stdout == ""
    assert stderr == ""
    run_directories = list(root.iterdir())
    assert len(run_directories) == 1
    assert (run_directories[0] / "market_history.json").exists()
    assert (run_directories[0] / "ma_crosses.json").exists()


def test_main_reads_multiline_json_until_end_marker() -> None:
    payload = {"datasets": [_dataset("M30", [10, 10, 10, 10, 20, 100])]}
    stdin_text = json.dumps(payload, indent=2) + "\n__END_JSON__\n"

    result, exit_code, stdout, stderr = _run_main(
        ["--input", "-", "--ma-periods", "2,3,4"],
        stdin_text=stdin_text,
    )

    assert result == 0
    assert exit_code is None
    assert stderr == ""
    assert len(json.loads(stdout)["crosses"]) == 2


def test_main_backtest_writes_all_result_files(tmp_path: Path) -> None:
    input_path = tmp_path / "source.json"
    run_directory = tmp_path / "data" / "find_ma_crosses" / "202608260202"
    closes = [10.0, 10.0, 10.0, 10.0, 20.0, 100.0, 100.0, 100.0]
    payload = {
        "datasets": [
            _dataset("M5", closes),
            _dataset("M15", closes),
            _dataset("M30", closes),
        ]
    }
    _write_payload(input_path, payload)

    result, exit_code, stdout, stderr = _run_main(
        [
            "--input",
            str(input_path),
            "--run-directory",
            str(run_directory),
            "--ma-periods",
            "2,3,4",
            "--backtest",
        ]
    )

    assert result == 0
    assert exit_code is None
    assert stdout == ""
    assert stderr == ""
    assert {path.name for path in run_directory.iterdir()} == {
        "market_history.json",
        "ma_crosses.json",
        "backtest_trades.json",
        "backtest_summary.json",
    }
    crosses = json.loads(
        (run_directory / "ma_crosses.json").read_text(encoding="utf-8")
    )
    trades = json.loads(
        (run_directory / "backtest_trades.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (run_directory / "backtest_summary.json").read_text(encoding="utf-8")
    )
    assert len(crosses["input_sha256"]) == 64
    assert crosses["input_sha256"] == trades["input_sha256"]
    assert crosses["input_sha256"] == summary["input_sha256"]
    assert summary["strategy_count"] == 62
    assert summary["settings"]["entry_condition"] == "M30_SMA2_SMA4_golden_cross"
    assert (
        summary["settings"]["entry_filter_condition"]
        == "M5_SMA2_gte_SMA4_and_M15_SMA2_gte_SMA4_at_entry"
    )
    assert summary["settings"]["cost_mode"] == "none"
    assert set(summary["strategies"][0]["periods"]) == {"overall"}
    assert "spread_adjusted" not in summary["strategies"][0]["periods"]["overall"]


def test_main_compares_m30_and_h1_entry_periods(tmp_path: Path) -> None:
    input_path = tmp_path / "source.json"
    run_directory = tmp_path / "data" / "find_ma_crosses" / "202608271600"
    closes = [10.0, 10.0, 10.0, 10.0, 20.0, 100.0, 100.0, 100.0]
    payload = {
        "datasets": [
            _dataset("M5", closes),
            _dataset("M15", closes),
            _dataset("M30", closes),
            _dataset("H1", closes),
        ]
    }
    _write_payload(input_path, payload)

    result, exit_code, stdout, stderr = _run_main(
        [
            "--input",
            str(input_path),
            "--run-directory",
            str(run_directory),
            "--ma-periods",
            "2,3,4",
            "--backtest",
            "--compare-entry-periods",
        ]
    )

    assert result == 0
    assert exit_code is None
    assert stdout == ""
    assert stderr == ""
    summary = json.loads(
        (run_directory / "backtest_summary.json").read_text(encoding="utf-8")
    )
    assert summary["strategy_count"] == 14
    assert set(summary["entry_signal_counts"]) == {"M30", "H1"}
    assert {strategy["entry_period"] for strategy in summary["strategies"]} == {
        "M30",
        "H1",
    }


def test_main_does_not_overwrite_existing_result_without_flag(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "source.json"
    output_path = tmp_path / "crosses.json"
    _write_payload(
        input_path,
        {"datasets": [_dataset("M30", [10, 10, 10, 10, 20, 100])]},
    )
    output_path.write_text('{"existing": true}\n', encoding="utf-8")

    result, exit_code, stdout, stderr = _run_main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--ma-periods",
            "2,3,4",
        ]
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "--overwrite" in stderr
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"existing": True}


def test_main_backtest_requires_run_directory(tmp_path: Path) -> None:
    input_path = tmp_path / "source.json"
    _write_payload(
        input_path,
        {"datasets": [_dataset("M30", [10, 10, 10, 10, 20, 100])]},
    )

    result, exit_code, stdout, stderr = _run_main(
        ["--input", str(input_path), "--backtest"]
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "--run-directory" in stderr


def test_main_rejects_using_input_as_output_even_with_overwrite(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "source.json"
    payload = {"datasets": [_dataset("M30", [10, 10, 10, 10, 20, 100])]}
    _write_payload(input_path, payload)

    result, exit_code, stdout, stderr = _run_main(
        [
            "--input",
            str(input_path),
            "--output",
            str(input_path),
            "--ma-periods",
            "2,3,4",
            "--overwrite",
        ]
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "同じパス" in stderr
    assert json.loads(input_path.read_text(encoding="utf-8")) == payload


def test_main_rejects_invalid_json(tmp_path: Path) -> None:
    input_path = tmp_path / "market_history.json"
    input_path.write_text("{invalid", encoding="utf-8")

    result, exit_code, stdout, stderr = _run_main(["--input", str(input_path)])

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "入力値は正しいJSON形式で指定してください" in stderr


def test_main_rejects_missing_datasets(tmp_path: Path) -> None:
    input_path = tmp_path / "market_history.json"
    _write_payload(input_path, {})

    result, exit_code, stdout, stderr = _run_main(["--input", str(input_path)])

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "datasets は1件以上の配列で指定してください" in stderr


def test_main_rejects_non_array_history(tmp_path: Path) -> None:
    input_path = tmp_path / "market_history.json"
    dataset = _dataset("M30", [10, 10, 10, 10])
    dataset["history"] = {"close": 10}
    _write_payload(input_path, {"datasets": [dataset]})

    result, exit_code, stdout, stderr = _run_main(
        ["--input", str(input_path), "--ma-periods", "2,3,4"]
    )

    assert result is None
    assert exit_code == 2
    assert stdout == ""
    assert "datasets[0].history は配列で指定してください" in stderr
