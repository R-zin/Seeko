from fastapi import APIRouter, HTTPException
from workers.notion_service import service

router = APIRouter(prefix="/api/notion")


def _err(e: Exception) -> dict:
    return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/test")
async def test():
    """Connect through the MCP server and compare the database with the tracker schema."""
    try: return await service.connect()
    except Exception as e: return _err(e)


@router.get("/status")
def status():
    return service.status()


@router.post("/setup")
async def setup():
    """Add every missing tracker property to the database (existing properties are left as they are)."""
    if not service.configured(): raise HTTPException(400, "set the Notion token and database ID first")
    try: return await service.setup()
    except Exception as e: return _err(e)


@router.post("/resync")
def resync():
    """Push every job again (pages deleted in Notion are recreated)."""
    return {"ok": True, "queued": service.resync()}


@router.post("/pull")
async def pull():
    """Read tracking edits back from Notion now."""
    if not service.configured(): raise HTTPException(400, "set the Notion token and database ID first")
    try: return {"ok": True, "changed": await service.pull()}
    except Exception as e: return _err(e)
