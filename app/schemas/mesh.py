from typing import Literal

from pydantic import StrictBool

from app.schemas.common import ApiModel, Parameters


class GenerateMeshRequest(Parameters):
    mesh_type: Literal["tetrahedron"] = "tetrahedron"
    element_order: Literal["first_order"] = "first_order"
    mesh_size_mode: Literal["level"] = "level"
    mesh_density: Literal["low", "medium", "high"] = "medium"
    mesh_option: Literal["conforming", "project_to_geometry", "periodic_boundary"] = "conforming"
    advanced_option_enable: StrictBool = False


class MeshResult(ApiModel):
    project_id: str
    mesh_id: str
    geometry_revision: int
    parameters: GenerateMeshRequest
