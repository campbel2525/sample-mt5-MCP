from unittest.mock import patch

import pytest

from scripts.send_message import main


def test_main_sends_only_slack_message() -> None:
    with (
        patch("scripts.send_message.settings") as mock_settings,
        patch("scripts.send_message.notify_slack") as mock_notify_slack,
        patch(
            "scripts.send_message.send_line_group_message"
        ) as mock_send_line_group_message,
    ):
        mock_settings.slack_web_hook_url_moving_average_notification = "webhook-url"

        result = main(["slack", "--message", "Slack message"])

    assert result == 0
    mock_notify_slack.assert_called_once_with(
        webhook_url="webhook-url",
        message="Slack message",
    )
    mock_send_line_group_message.assert_not_called()


def test_main_sends_only_line_group_message() -> None:
    with (
        patch("scripts.send_message.settings") as mock_settings,
        patch("scripts.send_message.notify_slack") as mock_notify_slack,
        patch(
            "scripts.send_message.send_line_group_message"
        ) as mock_send_line_group_message,
    ):
        mock_settings.line_channel_access_token = "channel-token"
        mock_settings.line_moving_average_notification_group_id = "group-id"

        result = main(["line", "--message", "LINE message"])

    assert result == 0
    mock_send_line_group_message.assert_called_once_with(
        channel_access_token="channel-token",
        group_id="group-id",
        texts=["LINE message"],
    )
    mock_notify_slack.assert_not_called()


@pytest.mark.parametrize(
    ("destination", "sender_name"),
    [
        ("slack", "notify_slack"),
        ("line", "send_line_group_message"),
    ],
)
def test_main_returns_one_when_sending_fails(
    destination: str,
    sender_name: str,
) -> None:
    with patch(
        f"scripts.send_message.{sender_name}",
        side_effect=RuntimeError("sending failed"),
    ):
        result = main([destination, "--message", "message"])

    assert result == 1


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["slack"],
        ["line"],
        ["unknown", "--message", "message"],
    ],
)
def test_main_rejects_missing_or_unknown_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    assert exc_info.value.code == 2
