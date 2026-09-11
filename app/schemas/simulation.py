from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.common import Identifier, Parameters


class SubmitSimulationRequest(Parameters):
    process_num: Annotated[int, Field(strict=True, ge=1)] = 1
    analyze_load_case_list: list[Identifier] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_cases(self):
        if len(set(self.analyze_load_case_list)) != len(self.analyze_load_case_list):
            raise ValueError("analyze_load_case_list cannot contain duplicates")
        return self
