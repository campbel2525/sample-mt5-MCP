"""Application settings using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Slack
    slack_web_hook_url_moving_average_notification: str = ""

    # LINE
    line_channel_access_token: str = ""
    line_recipient_id: str = ""

    # Debug / tooling
    debug_mode: bool = True
    debugpy_port: int = 0

    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s %(levelname)s %(name)s %(message)s"

    # line
    line_channel_access_token: str = ""  # LINEチャネルアクセストークン（長期）
    line_moving_average_notification_group_id: str = ""  # LINE通知先グループID
