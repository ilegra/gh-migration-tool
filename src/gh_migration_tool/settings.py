"""Environment-based configuration for credentials and platform endpoints."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    """Typed settings loaded from environment variables or .env file."""

    # Origin SCM credentials
    origin_token: str = Field("", validation_alias="ORIGIN_TOKEN")
    origin_username: str | None = Field(None, validation_alias="ORIGIN_USERNAME")

    # GitHub App credentials & endpoint
    github_app_id: str = Field("", validation_alias="GITHUB_APP_ID")
    github_app_installation_id: str = Field(
        "", validation_alias="GITHUB_APP_INSTALLATION_ID"
    )
    github_app_private_key: str = Field("", validation_alias="GITHUB_APP_PRIVATE_KEY")
    github_app_private_key_path: str | None = Field(
        None, validation_alias="GITHUB_APP_PRIVATE_KEY_PATH"
    )
    github_api_url: str = Field(
        "https://api.github.com", validation_alias="GITHUB_API_URL"
    )

    # Jenkins credentials & endpoint
    jenkins_url: str = Field("", validation_alias="JENKINS_URL")
    jenkins_username: str | None = Field(None, validation_alias="JENKINS_USERNAME")
    jenkins_token: str = Field("", validation_alias="JENKINS_TOKEN")

    # Google Sheets inventory configuration
    google_spreadsheet_id: str = Field("", validation_alias="GOOGLE_SPREADSHEET_ID")
    google_credentials_path: str | None = Field(
        None, validation_alias="GOOGLE_CREDENTIALS_PATH"
    )
    inventory_target_status: str = Field(
        "migrated", validation_alias="INVENTORY_TARGET_STATUS"
    )

    # Execution tuning
    max_workers: int = Field(4, validation_alias="MAX_WORKERS")
    ci_poll_interval_seconds: float = Field(
        5.0, validation_alias="CI_POLL_INTERVAL_SECONDS"
    )
    ci_poll_timeout_seconds: float = Field(
        300.0, validation_alias="CI_POLL_TIMEOUT_SECONDS"
    )
    http_timeout_seconds: float = Field(30.0, validation_alias="HTTP_TIMEOUT_SECONDS")
    http_max_retries: int = Field(3, validation_alias="HTTP_MAX_RETRIES")
    http_backoff_factor: float = Field(0.5, validation_alias="HTTP_BACKOFF_FACTOR")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )
