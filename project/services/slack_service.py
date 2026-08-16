from __future__ import annotations

import json
from urllib import error, request


def notify_slack(webhook_url: str, message: str) -> None:
    """Slack の Incoming Webhook へテキストを送信する

    概要:
        Webhook エンドポイントへ JSON ペイロードを POST する。

    引数:
        webhook_url: Slack で発行した Webhook URL
        message: 投稿する本文

    戻り値:
        なし（エラー時は例外を送出）
    """
    if not webhook_url:
        raise RuntimeError("Slack webhook URL is not configured.")
    payload = json.dumps({"text": message}).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Slack returned status {resp.status}")
    except error.URLError as exc:
        raise RuntimeError(f"Slack notification failed: {exc}") from exc
