from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://budgerr:budgerr@localhost:5433/budgerr"

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"

    cors_origins: str = "http://localhost:8081,http://localhost:3000"

    playstat_base_url: str = "http://localhost:8000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
