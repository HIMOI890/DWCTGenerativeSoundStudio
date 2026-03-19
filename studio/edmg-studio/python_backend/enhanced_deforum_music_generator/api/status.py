from fastapi import APIRouter

router = APIRouter()

@router.get("/")
async def get_status():
    """
    Return basic server status (for monitoring).
    """
    return {
        "status": "running",
        "service": "Enhanced Deforum Music Generator",
    }
