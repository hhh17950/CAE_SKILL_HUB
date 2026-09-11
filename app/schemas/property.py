from typing import Literal

from pydantic import Field, StrictBool

from app.schemas.common import ApiModel, Identifier, Parameters


class AdvancedOption(ApiModel):
    gauss_point_count: Literal["program_controlled"] = "program_controlled"
    element_tech: Literal["program_controlled"] = "program_controlled"


class CreatePropertyRequest(Parameters):
    entity_type: Literal["linear_solid"] = "linear_solid"
    material_ref: Identifier
    geometry_ref_enable: StrictBool = False
    material_coordinate_system: Literal["global"] = "global"
    advanced_option: AdvancedOption = Field(default_factory=AdvancedOption)


class PropertyResult(ApiModel):
    project_id: str
    property_id: str
    parameters: CreatePropertyRequest
