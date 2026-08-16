from __future__ import annotations

import json
from urllib import error, request
from typing import Dict, Any, List, Optional
import requests

LINE_PUSH_MESSAGE_URL = "https://api.line.me/v2/bot/message/push"

def send_line_group_message(
    channel_access_token: str,
    group_id: str,
    texts: List[str],
) -> Dict[str, Any]:
    """
    特定の LINE グループ（groupId 指定）にメッセージを送信するロジック。

    Args:
        channel_access_token (str): LINE Messaging API のチャネルアクセストークン（長期）
        group_id (str): 送信先グループの groupId
        texts (List[str]): 送信したいテキストメッセージのリスト
                           要素ごとに1バブルとして送信される

    Returns:
        Dict[str, Any]: LINE Platform からのレスポンス内容
    """

    # 認証ヘッダ
    headers: Dict[str, str] = {
        "Authorization": f"Bearer {channel_access_token}",
        "Content-Type": "application/json",
    }

    # メッセージ配列に整形
    messages: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": text,
        }
        for text in texts
    ]

    payload: Dict[str, Any] = {
        "to": group_id,  # ★ userId ではなく groupId を指定
        "messages": messages,
    }

    response: requests.Response = requests.post(
        LINE_PUSH_MESSAGE_URL,
        headers=headers,
        json=payload,
        timeout=10,
    )

    # レスポンスを dict に整形
    try:
        result: Dict[str, Any] = response.json()
    except ValueError:
        result = {
            "status_code": response.status_code,
            "body": response.text,
        }

    # ステータスコードチェック（必要なら例外投げてもOK）
    if response.status_code != 200:
        print("LINE 送信時にエラーが発生しました:")
        print("status_code:", response.status_code)
        print("response body:", response.text)
        print(response)

    return result
