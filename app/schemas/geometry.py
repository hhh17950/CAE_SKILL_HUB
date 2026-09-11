from typing import Annotated, Literal

from pydantic import Field

from app.schemas.common import ApiModel, Identifier, Parameters


class UploadedSource(ApiModel):
    type: Literal["file_id"]
    file_id: Identifier


class ServerPathSource(ApiModel):
    type: Literal["server_path"]
    path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class ImportGeometryRequest(Parameters):
    project_id: Identifier | None = None
    source: Annotated[UploadedSource | ServerPathSource, Field(discriminator="type")]


class GeometryResult(ApiModel):
    project_id: str
    geometry_id: str
    file_name: str
    file_suffix: str
    sha256: str
    geometry_revision: int
