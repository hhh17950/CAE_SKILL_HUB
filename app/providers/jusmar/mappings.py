"""Mapping to the supplied mock SDK vocabulary, NOT verified real SDK bindings."""

from app.schemas.load_case import AddLoadCaseRequest
from app.schemas.material import CreateMaterialRequest
from app.schemas.mesh import GenerateMeshRequest
from app.schemas.post_processing import PostProcessingRequest
from app.schemas.property import CreatePropertyRequest


def mesh_parameters(request: GenerateMeshRequest) -> dict:
    return {
        "mesh_type": {"tetrahedron": "四面体网格"}[request.mesh_type],
        "element_order": {"first_order": "一阶单元"}[request.element_order],
        "mesh_size_mode": {"level": "级别设置"}[request.mesh_size_mode],
        "mesh_density": {"low": "低", "medium": "中", "high": "高"}[request.mesh_density],
        "mesh_option": {
            "conforming": "协调网格",
            "project_to_geometry": "网格投影到几何",
            "periodic_boundary": "设置周期边界",
        }[request.mesh_option],
        "advanced_option_enable": request.advanced_option_enable,
    }


def material_parameters(request: CreateMaterialRequest) -> dict:
    params = request.model_dump(exclude={"extensions"})
    params["constitutive_model"] = {"linear_elastic": "线弹性"}[request.constitutive_model]
    return params


def property_parameters(request: CreatePropertyRequest) -> dict:
    params = request.model_dump(exclude={"extensions"})
    params.update(entity_type="线性实体", material_coordinate_system="全局坐标系")
    params["advanced_option"] = {"gaussPointCount": "程序控制", "elementTech": "程序控制"}
    return params


def load_case_parameters(request: AddLoadCaseRequest) -> dict:
    params = request.model_dump(exclude={"extensions"})
    params.update(load_case_type="原始工况", direct_control="采用全部")
    return params


def post_parameters(request: PostProcessingRequest) -> dict:
    return {
        "default_result_item": "模态位移",
        "output_items": [
            {"modal_displacement": "modalDisplacement", "reaction_force": "reactionForce"}[item]
            for item in request.output_items
        ],
    }
