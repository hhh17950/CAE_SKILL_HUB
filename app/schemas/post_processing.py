from typing import Literal

from pydantic import Field

from app.schemas.common import Parameters

ResultItem = Literal["modal_displacement", "reaction_force"]


class PostProcessingRequest(Parameters):
    default_result_item: Literal["modal_displacement"] = "modal_displacement"
    output_items: list[ResultItem] = Field(
        default_factory=lambda: ["modal_displacement", "reaction_force"], min_length=1, max_length=2
    )
