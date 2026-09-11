from typing import Literal

from pydantic import StrictBool

from app.schemas.common import ApiModel, Parameters


class AddLoadCaseRequest(Parameters):
    load_case_type: Literal["original"] = "original"
    add_load: StrictBool = False
    add_constraint: StrictBool = False
    add_contact: StrictBool = False
    direct_control: Literal["all"] = "all"


class LoadCaseResult(ApiModel):
    project_id: str
    load_case_id: str
    parameters: AddLoadCaseRequest
