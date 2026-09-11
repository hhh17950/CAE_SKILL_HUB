from typing import Annotated, Literal

from pydantic import Field

from app.schemas.common import ApiModel, Name, Parameters


class CreateMaterialRequest(Parameters):
    material_name: Name
    constitutive_model: Literal["linear_elastic"] = "linear_elastic"
    young_modulus: Annotated[float, Field(strict=True, gt=0, description="杨氏模量，Pa")] = 2e11
    poisson_ratio: Annotated[float, Field(strict=True, gt=-1, lt=0.5)] = 0.3
    density: Annotated[float, Field(strict=True, gt=0, description="密度，kg/m³")] = 7850


class MaterialResult(ApiModel):
    project_id: str
    material_id: str
    parameters: CreateMaterialRequest
