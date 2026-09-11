from app.schemas.common import ApiModel


class FileResult(ApiModel):
    file_id: str
    file_name: str
    file_suffix: str
    size_bytes: int
    sha256: str
