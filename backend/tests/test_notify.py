import httpx

from app import notify as notify_module
from app.notify import notify


def _capture_posts(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return object()

    monkeypatch.setattr(notify_module.httpx, "post", fake_post)
    return calls


def test_notify_noop_when_topic_unset(monkeypatch):
    monkeypatch.setattr(notify_module.settings, "ntfy_topic", "", raising=False)
    calls = _capture_posts(monkeypatch)

    notify("anything")

    assert calls == []


def test_notify_posts_to_topic_with_headers(monkeypatch):
    monkeypatch.setattr(notify_module.settings, "ntfy_topic", "budgerr-test", raising=False)
    monkeypatch.setattr(notify_module.settings, "ntfy_base_url", "https://ntfy.sh", raising=False)
    calls = _capture_posts(monkeypatch)

    notify("Parlay hit +$25", title="Bet won", tags="tada")

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://ntfy.sh/budgerr-test"
    assert kwargs["content"] == b"Parlay hit +$25"
    assert kwargs["headers"]["Title"] == "Bet won"
    assert kwargs["headers"]["Tags"] == "tada"


def test_notify_swallows_network_errors(monkeypatch):
    monkeypatch.setattr(notify_module.settings, "ntfy_topic", "budgerr-test", raising=False)

    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(notify_module.httpx, "post", boom)

    # Must not raise — a failed notification can't break the triggering request.
    notify("anything")
