import hashlib
from pathlib import Path

import anyio

from app.errors import DomainError, unsupported
from app.schemas.file import FileResult
from app.schemas.geometry import ServerPathSource, UploadedSource
from app.services.context import CallContext, Services
from app.storage.memory import StoredFile, new_id

SUFFIXES = {".stp", ".step", ".igs", ".iges", ".x_t"}


class FileService:
    def __init__(self, services: Services, directory: Path):
        self.s = services
        self.directory = directory

    def validate(self, filename: str, data: bytes):
        if (
            not filename
            or Path(filename).name != filename
            or "\\" in filename
            or len(filename) > 200
        ):
            raise DomainError("INVALID_FILE_NAME", "文件名必须是不含目录的有效名称", 422)
        if Path(filename).suffix.lower() not in SUFFIXES:
            raise unsupported("file", "Mock 仅接受 .stp/.step/.igs/.iges/.x_t 测试文件")
        if not data:
            raise DomainError("EMPTY_FILE", "文件不能为空", 422)
        if len(data) > self.s.settings.max_file_bytes:
            raise DomainError("FILE_TOO_LARGE", "文件超过大小限制", 413)

    async def save(self, call: CallContext, filename: str, data: bytes) -> FileResult:
        self.validate(filename, data)
        digest = hashlib.sha256(data).hexdigest()

        async def execute():
            self.s.store.check_capacity()
            file_id = new_id("file")
            suffix = Path(filename).suffix.lower()
            path = self.directory / (file_id + suffix)
            await anyio.Path(path).write_bytes(data)
            result = FileResult(
                file_id=file_id,
                file_name=filename,
                file_suffix=suffix,
                size_bytes=len(data),
                sha256=digest,
            )
            self.s.store.files[file_id] = StoredFile(call.owner, path, result)
            return result

        return await self.s.once(
            call, "upload_file", {"file_name": filename, "sha256": digest}, execute
        )

    async def resolve(self, owner: str, source: UploadedSource | ServerPathSource) -> StoredFile:
        if isinstance(source, UploadedSource):
            return self.s.store.file(owner, source.file_id)
        if not self.s.settings.allow_server_paths:
            raise unsupported("source.type", "本部署未启用服务端路径输入")
        root = self.s.settings.server_path_root.resolve()
        path = Path(source.path)
        if not path.is_absolute():
            raise unsupported("source.path", "必须使用允许目录中的绝对路径")
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise DomainError("FILE_ACCESS_DENIED", "文件不在允许目录中", 403)

        def read():
            if not resolved.is_file():
                raise DomainError("FILE_NOT_FOUND", "输入不是可读普通文件", 404)
            with resolved.open("rb") as stream:
                return stream.read(self.s.settings.max_file_bytes + 1)

        try:
            data = await anyio.to_thread.run_sync(read)
        except OSError as exc:
            raise DomainError("FILE_NOT_FOUND", "文件不可读取", 404) from exc
        # Stage a bounded immutable copy; later changes to source do not change this import.
        key = "path-" + hashlib.sha256(str(resolved).encode() + data).hexdigest()
        result = await self.save(CallContext(owner, key), resolved.name, data)
        return self.s.store.file(owner, result.file_id)
