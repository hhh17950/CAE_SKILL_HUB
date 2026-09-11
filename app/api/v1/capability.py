from fastapi import APIRouter, Request

from app.api.dependencies import ContainerDep, Owner, envelope
from app.schemas.capability import Capabilities
from app.schemas.common import Response
from app.services.file_service import SUFFIXES

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=Response[Capabilities], operation_id="get_capabilities")
async def get_capabilities(request: Request, identity: Owner, c: ContainerDep):
    result = Capabilities(
        operations=[
            "import_geometry",
            "generate_mesh",
            "create_material",
            "create_3d_property",
            "add_load_case",
            "solver_settings",
            "case_settings",
            "post_processing_settings",
            "submit_simulation",
        ],
        geometry_formats=sorted(SUFFIXES),
        limits={
            "max_file_bytes": c.shared.settings.max_file_bytes,
            "max_process_num": c.shared.settings.max_process_num,
            "max_analyzed_load_cases": 1,
            "max_records": c.shared.settings.max_records,
        },
        supported_parameters={
            "mesh_density": ["low", "medium", "high"],
            "mesh_option": ["conforming"],
            "element_order": ["first_order"],
            "advanced_option_enable": [False],
            "geometry_ref_enable": [False],
            "eigen_lower_bound": [False],
            "eigen_upper_bound": [False],
            "pre_stress_temp": [False],
            "eigen_count_enable": [True],
            "solver_type": ["mock_modal"],
            "linear_solver_type": ["mock_direct"],
            "temperature_and_normalization": None,
            "registered_extensions": [],
            "server_paths_enabled": c.shared.settings.allow_server_paths,
        },
        notes=[
            "仅模拟协议和状态，不执行 CAD 解析或 CAE 计算。",
            "内存状态与幂等记录仅在单进程生命周期内有效。",
            "已有工程重新导入在 Mock 中定义为替换：清空网格、属性、工况配置；保留材料、工况和历史任务。",
            "载荷/约束/接触开关仅记录布尔值，不代表实际物理定义。",
        ],
    )
    return envelope(request, result)
