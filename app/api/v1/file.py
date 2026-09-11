from fastapi import APIRouter, Request, Response, UploadFile

from app.api.dependencies import Call, ContainerDep, envelope
from app.errors import DomainError
from app.schemas.common import Response as Envelope
from app.schemas.file import FileResult

router = APIRouter(tags=["files"])


@router.post(
    "/files",
    response_model=Envelope[FileResult],
    status_code=201,
    operation_id="upload_file",
    summary="上传 Mock 几何文件",
)
async def upload_file(
    request: Request, response: Response, file: UploadFile, call: Call, c: ContainerDep
):
    limit = c.shared.settings.max_file_bytes
    data = bytearray()
    try:
        while chunk := await file.read(64 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                raise DomainError("FILE_TOO_LARGE", "文件超过大小限制", 413)
        result = await c.files.save(call, file.filename or "", bytes(data))
    finally:
        await file.close()
    return envelope(request, result)
