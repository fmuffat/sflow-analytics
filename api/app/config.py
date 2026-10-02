"""API settings, read from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_name: str = "sFlow Analytics"
    app_version: str = "dev"
    app_timezone: str = "Europe/Paris"

    clickhouse_host: str = "clickhouse"
    clickhouse_http_port: int = 8123
    clickhouse_database: str = "sflow"
    clickhouse_user: str = "sflow"
    clickhouse_password: str = ""

    collector_status_url: str = "http://collector:8081"

    # Configuration store and authentication
    config_dir: str = "/config"
    auth_enabled: bool = True
    admin_username: str = "admin"
    admin_password: str = ""          # empty: random password generated at first start
    session_idle_minutes: int = 60
    session_max_hours: int = 12
    cookie_secure: bool = True        # cookies only over HTTPS (nginx)
    secret_key: str = ""              # Fernet key for secrets at rest; empty: key file in config_dir
    retention_days: int = 90

    # Guard rails for analytics queries.
    max_limit: int = 1000
    default_limit: int = 10
    max_timeseries_points: int = 1000
    query_timeout_seconds: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
