from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The ONLY coupling to Postgres: plain SQLAlchemy over a URL, with alembic
    # owning the schema. Keep it that way — no supabase-py, no PostgREST, no RLS
    # the app depends on. That is what keeps "move to a different Postgres" a
    # one-variable change instead of a rewrite.
    #
    # On Supabase, use the SESSION pooler (port 5432 on ...pooler.supabase.com).
    # Not the direct connection: IPv6-only on recent projects, so an IPv4-only
    # host times out in a way that reads like a firewall problem.
    # Not the transaction pooler (6543): it breaks psycopg3's prepared
    # statements, which surfaces as intermittent 'prepared statement "_pg_..."
    # already exists' under reuse rather than a clean failure at startup.
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
