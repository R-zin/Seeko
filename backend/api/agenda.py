from fastapi import APIRouter, HTTPException
import agenda
import notify
from config.settings import get_settings

router = APIRouter(prefix="/api/agenda")


@router.get("")
def get_agenda():
    """Next steps due, applications to follow up, deadlines, and what is waiting for you."""
    return agenda.build()


@router.post("/digest")
async def send_digest():
    """Send today's digest now (it also goes out by itself once a day at DIGEST_HOUR)."""
    if not get_settings().notify_webhook_url.strip(): raise HTTPException(400, "set a webhook URL first (Setup → Notifications)")
    text = agenda.digest_text(agenda.build()) or "Nothing due: no next steps, follow-ups or closing deadlines."
    if not await notify.send("digest", "Job search digest", text, notify.link("tracker"), force=True):
        raise HTTPException(502, "the webhook did not accept the message (see the log)")
    return {"ok": True}
