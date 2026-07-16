"""Best-effort push notifications via ntfy (https://ntfy.sh).

A single-user, zero-setup notification channel: the backend POSTs a message to
`{NTFY_BASE_URL}/{NTFY_TOPIC}` and the phone (subscribed to that topic in the
ntfy app) receives it. Disabled entirely when `NTFY_TOPIC` is unset, and never
raises — a failed or slow notification must never break the request that
triggered it (settlement, auto-log, budget recompute).
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def notify(
    message: str,
    *,
    title: str | None = None,
    tags: str | None = None,
    priority: str | None = None,
) -> None:
    """Send a push notification. No-op when NTFY_TOPIC is unset; swallows all
    network errors (logging a warning) so callers never have to handle them.

    tags is a comma-separated list of ntfy tags/emoji (e.g. "tada,moneybag").
    """
    topic = settings.ntfy_topic.strip()
    if not topic:
        return

    headers: dict[str, str] = {}
    if title:
        headers["Title"] = title
    if tags:
        headers["Tags"] = tags
    if priority:
        headers["Priority"] = priority

    url = f"{settings.ntfy_base_url.rstrip('/')}/{topic}"
    try:
        httpx.post(url, content=message.encode("utf-8"), headers=headers, timeout=5.0)
    except httpx.HTTPError as exc:
        logger.warning("ntfy notification failed: %s", exc)
