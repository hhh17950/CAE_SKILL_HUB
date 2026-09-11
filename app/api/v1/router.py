from fastapi import APIRouter

from app.api.v1 import (
    capability,
    case_settings,
    file,
    geometry,
    load_case,
    material,
    mesh,
    operation,
    post_processing,
    project,
    property,
    simulation,
    solver,
)
from app.schemas.common import Problem

ERROR_RESPONSES = {
    status: {
        "model": Problem,
        "description": description,
        "content": {"application/problem+json": {}},
    }
    for status, description in {
        401: "身份无效",
        403: "无访问权限",
        404: "资源不存在或不可见",
        409: "状态或幂等冲突",
        413: "文件或请求过大",
        422: "参数非法或能力不支持",
        429: "容量限制",
        500: "服务错误",
        502: "模拟底层失败",
        503: "服务不可用",
    }.items()
}
router = APIRouter(responses=ERROR_RESPONSES)
router.include_router(geometry.router)
router.include_router(mesh.router)
router.include_router(material.router)
router.include_router(property.router)
router.include_router(load_case.router)
router.include_router(solver.router)
router.include_router(case_settings.router)
router.include_router(post_processing.router)
router.include_router(simulation.router)
router.include_router(project.router)
router.include_router(file.router)
router.include_router(operation.router)
router.include_router(capability.router)
