import plaid
from plaid.api import plaid_api

from app.config import settings

_ENV_HOSTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


class PlaidNotConfiguredError(RuntimeError):
    pass


def get_plaid_client() -> plaid_api.PlaidApi:
    if not settings.plaid_client_id or not settings.plaid_secret:
        raise PlaidNotConfiguredError(
            "PLAID_CLIENT_ID / PLAID_SECRET are not set. Add them to backend/.env "
            "(see backend/.env.example)."
        )

    configuration = plaid.Configuration(
        host=_ENV_HOSTS[settings.plaid_env],
        api_key={
            "clientId": settings.plaid_client_id,
            "secret": settings.plaid_secret,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)
