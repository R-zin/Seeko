from fastapi import APIRouter, HTTPException
import notify
from config.settings import get_settings

router = APIRouter(prefix="/api/notify")


@router.get("/events")
def events():
    return [{"key": k, "label": v} for k, v in notify.EVENTS.items()]


@router.post("/test")
async def test():
    """Send a test message to the configured webhook now."""
    url = get_settings().notify_webhook_url.strip()
    if not url: raise HTTPException(400, "set a webhook URL first")
    ok = await notify.send("test", "Seeko test notification", "Notifications work. You will hear about forms waiting "
                           "for your approval, failures and new jobs here.", notify.link(), force=True)
    if not ok: raise HTTPException(502, f"the {notify.kind_of(url)} webhook did not accept the message (see the log)")
    return {"ok": True, "kind": notify.kind_of(url)}
