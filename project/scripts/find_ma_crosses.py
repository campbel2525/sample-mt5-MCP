"""MCPの複数時間足OHLCV JSONから全期間のMAクロスを抽出する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import IO, List, Mapping, Optional, Sequence, Tuple, cast

from services.backtest_service import DEFAULT_PROFIT_TARGETS, run_exit_backtest
from services.chart_service import (
    normalize_applied_price,
    normalize_moving_average_method,
)
from services.cross_scan_service import (
    CrossScanResult,
    MovingAverageCross,
    scan_moving_average_crosses,
)

DATETIME_FORMAT = "%Y.%m.%d %H:%M:%S"
DEFAULT_RUN_DIRECTORY_ROOT = (
    Path("..") / "data" / "タスク" / "2_Gold売買検証" / "1_Goldの売買タイミング"
)


def _parse_periods(value: str) -> Sequence[int]:
    try:
        periods = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "移動平均の期間はカンマ区切りの整数で指定してください"
        ) from exc
    if len(periods) != 3 or any(period <= 0 for period in periods):
        raise argparse.ArgumentTypeError(
            "移動平均の期間は短期,中期,長期の3つを指定してください"
        )
    if len(set(periods)) != len(periods):
        raise argparse.ArgumentTypeError("移動平均の期間は重複できません")
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


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.strptime(value, DATETIME_FORMAT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "日時は YYYY.MM.DD HH:MM:SS 形式で指定してください"
        ) from exc


def _non_negative_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "末尾バーシフトは0以上の整数で指定してください"
        ) from exc
    if number < 0:
        raise argparse.ArgumentTypeError(
            "末尾バーシフトは0以上の整数で指定してください"
        )
    return number


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("0より大きい数値で指定してください") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("0より大きい数値で指定してください")
    return number


def _digits(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "価格の小数桁数は0以上の整数で指定してください"
        ) from exc
    if number < 0:
        raise argparse.ArgumentTypeError(
            "価格の小数桁数は0以上の整数で指定してください"
        )
    return number


def _split_ratio(value: str) -> float:
    number = _positive_float(value)
    if number >= 1:
        raise argparse.ArgumentTypeError("0より大きく1より小さい数値で指定してください")
    return number


def _parse_profit_targets(value: str) -> Sequence[float]:
    try:
        targets = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "利益率はカンマ区切りの数値で指定してください"
        ) from exc
    if not targets or any(target <= 0 for target in targets):
        raise argparse.ArgumentTypeError("利益率は0より大きい数値で指定してください")
    if len(set(targets)) != len(targets):
        raise argparse.ArgumentTypeError("利益率は重複しないように指定してください")
    return targets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="複数時間足の全履歴から移動平均線のクロスを抽出します。"
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="入力JSONファイル。-を指定すると標準入力から読み込みます。",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="出力JSONファイル。-を指定すると標準出力へ出力します。",
    )
    parser.add_argument(
        "--run-directory",
        nargs="?",
        const="AUTO",
        metavar="PATH",
        help=(
            "入力と出力を保存する実行フォルダ。値を省略すると"
            "data/タスク/2_Gold売買検証/1_Goldの売買タイミング/"
            "YYYYMMDDHHmmを作成します。"
        ),
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
        help="MAの適用価格（既定: CLOSE）",
    )
    parser.add_argument(
        "--end-bar-shift",
        type=_non_negative_integer,
        default=1,
        metavar="SHIFT",
        help="走査対象から除外する末尾バー数（既定: 1）",
    )
    parser.add_argument(
        "--scan-from",
        type=_parse_datetime,
        metavar="DATETIME",
        help="出力対象の開始日時（含む）",
    )
    parser.add_argument(
        "--scan-to",
        type=_parse_datetime,
        metavar="DATETIME",
        help="出力対象の終了日時（含まない）",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="M30の買いシグナルに対する売却条件を検証します。",
    )
    parser.add_argument(
        "--moving-average-exits-only",
        action="store_true",
        help="M5、M15、M30のデッドクロス6条件だけを検証します。",
    )
    parser.add_argument(
        "--profit-targets",
        type=_parse_profit_targets,
        default=DEFAULT_PROFIT_TARGETS,
        metavar="PERCENTAGES",
        help="利確率の一覧（既定: 0.25,0.5,0.75,1,1.5,2,3,5）",
    )
    parser.add_argument(
        "--point-size",
        type=_positive_float,
        default=0.01,
        metavar="SIZE",
        help="1ポイントの価格幅（既定: 0.01）",
    )
    parser.add_argument(
        "--digits",
        type=_digits,
        default=2,
        metavar="DIGITS",
        help="価格の小数桁数（既定: 2）",
    )
    parser.add_argument(
        "--cost-mode",
        choices=("none", "spread", "both"),
        default="none",
        help="集計するコスト条件（既定: none）",
    )
    parser.add_argument(
        "--split-ratio",
        type=_split_ratio,
        default=0.7,
        metavar="RATIO",
        help="期間を前半と後半に分ける位置（既定: 0.7）",
    )
    parser.add_argument(
        "--expected-symbol",
        default="GOLD",
        metavar="SYMBOL",
        help="バックテスト対象シンボル（既定: GOLD）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存の出力ファイルを置き換えます。",
    )
    return parser


def _read_payload(path: str) -> Mapping[str, object]:
    source: IO[str]
    if path == "-":
        source = sys.stdin
        payload_lines = []
        for line in source:
            if line.rstrip("\r\n") == "__END_JSON__":
                break
            payload_lines.append(line)
        payload_text = "".join(payload_lines)
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError("入力値は正しいJSON形式で指定してください") from exc
    else:
        source = Path(path).open(encoding="utf-8")
        try:
            payload = json.load(source)
        except json.JSONDecodeError as exc:
            raise ValueError("入力値は正しいJSON形式で指定してください") from exc
        finally:
            source.close()
    if not isinstance(payload, Mapping):
        raise ValueError("入力JSONの最上位はオブジェクトで指定してください")
    return cast(Mapping[str, object], payload)


def _read_datasets(payload: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    datasets = payload.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("datasets は1件以上の配列で指定してください")
    result = []
    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, Mapping):
            raise ValueError(f"datasets[{index}] はオブジェクトで指定してください")
        result.append(cast(Mapping[str, object], dataset))
    return result


def _read_dataset_text(
    dataset: Mapping[str, object],
    key: str,
    index: int,
) -> str:
    value = dataset.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"datasets[{index}].{key} は空でない文字列で指定してください")
    return value


def _scan_datasets(
    datasets: Sequence[Mapping[str, object]],
    moving_average_periods: Sequence[int],
    moving_average_method: str,
    applied_price: str,
    end_bar_shift: int,
    scan_from: Optional[datetime],
    scan_to: Optional[datetime],
) -> Sequence[CrossScanResult]:
    results = []
    for index, dataset in enumerate(datasets):
        symbol = _read_dataset_text(dataset, "symbol", index)
        period = _read_dataset_text(dataset, "period", index)
        history = dataset.get("history")
        if not isinstance(history, list):
            raise ValueError(f"datasets[{index}].history は配列で指定してください")
        results.append(
            scan_moving_average_crosses(
                symbol=symbol,
                period=period,
                history=history,
                moving_average_periods=moving_average_periods,
                moving_average_method=moving_average_method,
                applied_price=applied_price,
                end_bar_shift=end_bar_shift,
                scan_from=scan_from,
                scan_to=scan_to,
            )
        )
    return results


def _format_datetime(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)


def _cross_to_dict(cross: MovingAverageCross) -> Mapping[str, object]:
    return {
        "symbol": cross.symbol,
        "period": cross.period,
        "bar_time": _format_datetime(cross.bar_time),
        "type": cross.cross_type,
        "short_period": cross.short_period,
        "comparison_period": cross.comparison_period,
        "previous_short_ma": cross.previous_short_ma,
        "previous_comparison_ma": cross.previous_comparison_ma,
        "current_short_ma": cross.current_short_ma,
        "current_comparison_ma": cross.current_comparison_ma,
        "close": cross.close,
    }


def _build_output(
    results: Sequence[CrossScanResult],
    moving_average_periods: Sequence[int],
    moving_average_method: str,
    applied_price: str,
    end_bar_shift: int,
    scan_from: Optional[datetime],
    scan_to: Optional[datetime],
    input_sha256: Optional[str] = None,
) -> Mapping[str, object]:
    crosses = [cross for result in results for cross in result.crosses]
    crosses.sort(
        key=lambda cross: (
            cross.bar_time,
            cross.period,
            cross.short_period,
            cross.comparison_period,
        )
    )
    return {
        "schema_version": "1.0",
        "input_sha256": input_sha256,
        "settings": {
            "moving_average_method": moving_average_method,
            "applied_price": applied_price,
            "periods": list(moving_average_periods),
            "end_bar_shift": end_bar_shift,
            "scan_from": _format_datetime(scan_from) if scan_from else None,
            "scan_to": _format_datetime(scan_to) if scan_to else None,
        },
        "datasets": [
            {
                "symbol": result.symbol,
                "period": result.period,
                "first_bar_time": _format_datetime(result.first_bar_time),
                "last_bar_time": _format_datetime(result.last_bar_time),
                "bar_count": result.bar_count,
                "cross_count": len(result.crosses),
            }
            for result in results
        ],
        "crosses": [_cross_to_dict(cross) for cross in crosses],
    }


def _payload_sha256(payload: Mapping[str, object]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _write_json_atomic(
    path: str,
    output: Mapping[str, object],
) -> None:
    if path == "-":
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2, allow_nan=False)
        sys.stdout.write("\n")
        return

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                output,
                temporary,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _prepare_run_directory(value: Optional[str]) -> Optional[Path]:
    if value is None:
        return None
    if value == "AUTO":
        value = str(DEFAULT_RUN_DIRECTORY_ROOT / datetime.now().strftime("%Y%m%d%H%M"))
    run_directory = Path(value)
    run_directory.mkdir(parents=True, exist_ok=True)
    return run_directory


def _is_same_path(left: str, right: Path) -> bool:
    if left == "-":
        return False
    return Path(left).resolve() == right.resolve()


def _validate_output_targets(
    targets: Sequence[Tuple[str, Mapping[str, object]]],
    overwrite: bool,
    input_path: str,
    skipped_paths: Sequence[Path] = (),
) -> None:
    skipped = {path.resolve() for path in skipped_paths}
    protected_input = Path(input_path).resolve() if input_path != "-" else None
    seen = set()
    existing = []
    for path, _ in targets:
        if path == "-":
            continue
        candidate = Path(path)
        resolved = candidate.resolve()
        if resolved in skipped:
            continue
        if protected_input is not None and resolved == protected_input:
            raise ValueError("入力ファイルと出力ファイルに同じパスは指定できません")
        if resolved in seen:
            raise ValueError(f"同じ出力先が重複しています: {candidate}")
        seen.add(resolved)
        if not overwrite and candidate.exists():
            existing.append(str(candidate))
    if existing:
        raise ValueError(
            "出力先が既に存在します。--overwrite を指定してください: "
            + ", ".join(existing)
        )


def _validate_backtest_settings(args: argparse.Namespace) -> None:
    if not args.backtest:
        return
    if args.run_directory is None:
        raise ValueError("--backtest には --run-directory の指定が必要です")
    if args.ma_method != "SMA":
        raise ValueError("バックテストの移動平均方式は SMA を指定してください")
    if args.applied_price != "CLOSE":
        raise ValueError("バックテストの適用価格は CLOSE を指定してください")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        _validate_backtest_settings(args)
        run_directory = _prepare_run_directory(args.run_directory)
        payload = _read_payload(args.input)
        datasets = _read_datasets(payload)
        input_sha256 = _payload_sha256(payload)
        results = _scan_datasets(
            datasets=datasets,
            moving_average_periods=args.ma_periods,
            moving_average_method=args.ma_method,
            applied_price=args.applied_price,
            end_bar_shift=args.end_bar_shift,
            scan_from=args.scan_from,
            scan_to=args.scan_to,
        )
        output = _build_output(
            results=results,
            moving_average_periods=args.ma_periods,
            moving_average_method=args.ma_method,
            applied_price=args.applied_price,
            end_bar_shift=args.end_bar_shift,
            scan_from=args.scan_from,
            scan_to=args.scan_to,
            input_sha256=input_sha256,
        )
        output_path = args.output or "-"
        writes: List[Tuple[str, Mapping[str, object]]] = []
        skipped_paths: List[Path] = []
        if run_directory is not None:
            market_path = run_directory / "market_history.json"
            if _is_same_path(args.input, market_path):
                skipped_paths.append(market_path)
            else:
                writes.append((str(market_path), payload))
            if args.output is None:
                output_path = str(run_directory / "ma_crosses.json")
        writes.append((output_path, output))

        if args.backtest:
            assert run_directory is not None
            backtest = run_exit_backtest(
                datasets=datasets,
                cross_results=results,
                moving_average_periods=args.ma_periods,
                moving_average_method=args.ma_method,
                applied_price=args.applied_price,
                profit_targets=args.profit_targets,
                moving_average_exits_only=args.moving_average_exits_only,
                point_size=args.point_size,
                digits=args.digits,
                cost_mode=args.cost_mode,
                end_bar_shift=args.end_bar_shift,
                scan_from=args.scan_from,
                scan_to=args.scan_to,
                split_ratio=args.split_ratio,
                expected_symbol=args.expected_symbol,
                input_sha256=input_sha256,
            )
            writes.extend(
                (
                    (str(run_directory / "backtest_trades.json"), backtest.trades),
                    (str(run_directory / "backtest_summary.json"), backtest.summary),
                )
            )

        _validate_output_targets(
            targets=writes,
            overwrite=args.overwrite,
            input_path=args.input,
            skipped_paths=skipped_paths,
        )
        for path, document in writes:
            _write_json_atomic(path, document)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
