from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def health():
    return {
        "status": "ok",
        "execution_mode": "test-script",
        "storage_mode": "sqlite",
        "worker_mode": "separate_process",
    }
