from fastapi import APIRouter
from workers import notion_service

router = APIRouter(prefix="/api/notion")


@router.post("/test")
async def test():
    try: return await notion_service.connect()
    except Exception as e: return {"ok": False, "error": f"{type(e).__name__}: {e}"}
