import logging
import sys
from typing import Union

_LOGGER_INITIALIZED = False


def _resolve_level(level: Union[int, str]) -> int:
    """数値/文字列いずれのログレベル指定でも整数レベルへ変換する"""
    if isinstance(level, str):
        return getattr(logging, level.upper(), logging.INFO)
    return level


def setup_logger(
    name: str,
    *,
    level: Union[int, str] = logging.INFO,
    fmt: str = "%(asctime)s %(levelname)s %(name)s %(message)s",
) -> logging.Logger:
    """共通設定のロガーを作成または取得する

    Args:
        name: ロガー名（通常は __name__）
        level: ログレベル（数値 or 文字列）
        fmt: ハンドラに適用するログフォーマット
    """

    global _LOGGER_INITIALIZED
    resolved_level = _resolve_level(level)
    root_logger = logging.getLogger()

    if not _LOGGER_INITIALIZED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt))
        root_logger.handlers.clear()
        root_logger.addHandler(handler)
        _LOGGER_INITIALIZED = True
    else:
        formatter = logging.Formatter(fmt)
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

    root_logger.setLevel(resolved_level)
    logger = logging.getLogger(name)
    logger.setLevel(resolved_level)
    return logger
