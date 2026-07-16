from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://budgerr:budgerr@localhost:5433/budgerr"

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"

    cors_origins: str = "http://localhost:8081,http://localhost:3000"

    playstat_base_url: str = "http://localhost:8000"
    # playstat enforces X-API-Key auth when its AUTH_ENABLED is set; provision
    # a "budgerr" key in playstat's PLAYSTAT_API_KEYS and mirror it here.
    playstat_api_key: str = ""

    anthropic_api_key: str = ""

    # Push notifications via ntfy. Empty NTFY_TOPIC disables them entirely.
    # ntfy.sh topics are public, so use a hard-to-guess topic name.
    ntfy_base_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
