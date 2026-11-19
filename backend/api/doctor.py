from fastapi import APIRouter
import doctor

router = APIRouter(prefix="/api")


@router.get("/doctor")
async def readiness():
    """What still stands between you and a running automation, with how to fix each item."""
    return await doctor.report()
