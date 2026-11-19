import asyncio
from fastapi import APIRouter, HTTPException
from backup import service

router = APIRouter(prefix="/api/backup")


@router.get("")
def status():
    return service.status()


@router.post("/sync")
async def sync():
    """Run a full check against the backup server now."""
    if not service.configured(): raise HTTPException(400, "BACKUP_DATABASE_URL is not set")
    try: stats = await asyncio.to_thread(service.sync, True)
    except Exception as e: raise HTTPException(502, f"{type(e).__name__}: {e}")
    return {"ok": True, "tables": stats, **service.status()}
