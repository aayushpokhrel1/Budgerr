import pytest

from app import auth


@pytest.fixture(autouse=True)
def _auth_disabled_by_default(monkeypatch):
    """Force API auth off as the test baseline.

    `app.auth` reads `AUTH_ENABLED` from the environment (via `load_dotenv`) at
    import time. Once the live service has `AUTH_ENABLED=true` in `backend/.env`,
    a `TestClient` request without a key would 401 and break unrelated route
    tests (e.g. the playstat-proxy suite). Pin auth off here so the suite is
    deterministic regardless of the ambient `.env`; tests that exercise auth
    (`test_auth.py`) monkeypatch it back on explicitly.
    """
    monkeypatch.setattr(auth, "AUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(auth, "API_KEYS", {}, raising=False)
