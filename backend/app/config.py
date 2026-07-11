from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://budgerr:budgerr@localhost:5433/budgerr"

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"


settings = Settings()
