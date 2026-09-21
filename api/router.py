"""Central route registration; transport helpers stay outside routes/."""

from fastapi import APIRouter

from api.routes.docs import router as docs_router
from api.routes.health import router as health_router
from api.routes.runs import router as runs_router
from api.schemas import Problem

ERROR_RESPONSES = {code: {"model": Problem} for code in (401, 404, 409, 413, 422, 500)}

router = APIRouter()
router.include_router(runs_router, prefix="/api/v1", responses=ERROR_RESPONSES)
router.include_router(health_router)
router.include_router(docs_router)
