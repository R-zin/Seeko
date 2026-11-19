"""Notifications: tell you when something needs you (a filled form waiting for approval) or happened (applied
on its own, failed, a search found jobs), plus a daily follow-up digest.

One webhook URL (NOTIFY_WEBHOOK_URL), shaped for where it points: a Slack or Discord incoming webhook, an ntfy topic
(https://ntfy.sh/<topic>, or your own ntfy server), or any other URL, which gets plain JSON. Sending is best-effort
and never blocks or breaks the pipeline; the same message is not repeated within 10 minutes.
"""
from __future__ import annotations
import asyncio
import time
from urllib.parse import urlsplit
import httpx
from config.settings import get_settings

EVENTS = {
    "needs_review": "A form is filled and waiting for your approval (or your answers)",
    "applied": "Seeko submitted an application on its own (full-auto)",
    "failed": "A job failed and needs a look",
    "new_jobs": "A search found new jobs",
    "digest": "Daily digest: follow-ups due, next steps, deadlines",
}
DEDUPE_S = 600
_recent: dict[tuple, float] = {}
_tasks: set[asyncio.Task] = set()
_last_error_logged = 0.0


def client() -> httpx.AsyncClient:   # tests swap this for a mock transport
    return httpx.AsyncClient(timeout=10, follow_redirects=True)


def kind_of(url: str) -> str:
    p = urlsplit(url)
    host = p.netloc.lower()
    if host == "hooks.slack.com": return "slack"
    if host in ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com") and "/api/webhooks/" in p.path:
        return "discord"
    if host == "ntfy.sh" or host.startswith("ntfy."): return "ntfy"
    return "json"


def link(path: str = "") -> str:
    return get_settings().app_url.rstrip("/") + ("/#" + path if path else "/")


def request(url: str, event: str, title: str, text: str, href: str | None, data: dict | None) -> tuple[str, dict]:
    """(post URL, JSON body) for a message, in the shape the destination expects."""
    kind = kind_of(url)
    if kind == "slack":
        return url, {"text": f"*{title}*\n{text}" + (f"\n<{href}|Open in Seeko>" if href else "")}
    if kind == "discord":
        return url, {"content": (f"**{title}**\n{text}" + (f"\n{href}" if href else ""))[:2000],
                     "allowed_mentions": {"parse": []}}
    if kind == "ntfy":   # JSON publishing: POST to the server root with the topic in the body
        p = urlsplit(url)
        topic = p.path.strip("/").split("/")[0]
        body = {"topic": topic, "title": title[:250], "message": text[:4000], "tags": [event]}
        if href: body["click"] = href
        return f"{p.scheme}://{p.netloc}/", body
    return url, {"event": event, "title": title, "text": text, "url": href, "data": data or {}}


async def send(event: str, title: str, text: str = "", href: str | None = None, data: dict | None = None,
               force: bool = False) -> bool:
    """Deliver one message now. Returns whether the destination accepted it. `force` ignores the event filter and
    the repeat guard (used by the Test button)."""
    global _last_error_logged
    st = get_settings()
    url = st.notify_webhook_url.strip()
    if not url or (not force and event not in st.notify_events):
        return False
    key = (event, title, text)
    now = time.monotonic()
    if not force and now - _recent.get(key, -DEDUPE_S) < DEDUPE_S:
        return False
    _recent[key] = now
    for k in [k for k, t in _recent.items() if now - t > DEDUPE_S]: _recent.pop(k, None)
    post_url, body = request(url, event, title, text, href, data)
    try:
        async with client() as c:
            r = await c.post(post_url, json=body)
        ok = r.status_code < 300
        err = "" if ok else f"HTTP {r.status_code}: {r.text[:200]}"
    except httpx.HTTPError as e:
        ok, err = False, f"{type(e).__name__}: {e}"
    if not ok and (force or now - _last_error_logged > 3600):
        _last_error_logged = now
        from db import log
        try: log(f"notification not delivered ({kind_of(url)}): {err}", "warn")
        except Exception: pass
    return ok


def emit(event: str, title: str, text: str = "", href: str | None = None, data: dict | None = None) -> None:
    """Fire-and-forget from anywhere in the app (never raises, never waits)."""
    st = get_settings()
    if not st.notify_webhook_url.strip() or event not in st.notify_events:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:     # no event loop here (a worker thread, the CLI): deliver synchronously
        try: asyncio.run(send(event, title, text, href, data))
        except Exception: pass
        return
    task = loop.create_task(send(event, title, text, href, data))
    _tasks.add(task); task.add_done_callback(_tasks.discard)
