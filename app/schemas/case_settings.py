from typing import Annotated, Literal

from pydantic import Field, StrictBool

from app.schemas.common import Identifier, Parameters


class CaseSettingsRequest(Parameters):
    selected_load_case: Identifier
    pre_stress_temp: StrictBool = False
    init_thermal_stress_temp: None = None
    material_property_temp: None = None
    eigen_method: Literal["lanczos"] = "lanczos"
    eigen_lower_bound: StrictBool = False
    eigen_upper_bound: StrictBool = False
    eigen_count_enable: StrictBool = True
    eigen_count_value: Annotated[int, Field(strict=True, ge=1, le=10000)] = 10
    eigen_normalize_method: None = None
