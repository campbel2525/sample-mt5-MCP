"""
実行方法:

Slack:
    pipenv run python scripts/send_message.py slack --message "Slackへのメッセージ"

LINE:
    pipenv run python scripts/send_message.py line --message "LINEへのメッセージ"
"""

from __future__ import annotations

import argparse
from typing import List, Optional

from config.custom_logger import setup_logger
from config.settings import Settings
from services.line_service import send_line_group_message
from services.slack_service import notify_slack


settings = Settings()
logger = setup_logger(__name__, level=settings.log_level, fmt=settings.log_format)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="外部から受け取ったメッセージを Slack または LINE へ送信する。"
    )
    subparsers = parser.add_subparsers(dest="destination", required=True)

    slack_parser = subparsers.add_parser("slack", help="Slack へ送信する。")
    slack_parser.add_argument(
        "--message",
        required=True,
        help="Slack へ送信するメッセージ。",
    )

    line_parser = subparsers.add_parser("line", help="LINE へ送信する。")
    line_parser.add_argument(
        "--message",
        required=True,
        help="LINE へ送信するメッセージ。",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        if args.destination == "slack":
            notify_slack(
                webhook_url=settings.slack_web_hook_url_moving_average_notification,
                message=args.message,
            )
        else:
            send_line_group_message(
                channel_access_token=settings.line_channel_access_token,
                group_id=settings.line_moving_average_notification_group_id,
                texts=[args.message],
            )
    except RuntimeError as exc:
        logger.error("Message sending failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
