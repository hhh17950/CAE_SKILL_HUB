from dataclasses import dataclass, field


@dataclass
class DomainError(Exception):
    code: str
    detail: str
    status: int = 409
    retryable: bool = False
    field_errors: list[dict[str, str]] = field(default_factory=list)


def not_found(resource: str) -> DomainError:
    return DomainError("RESOURCE_NOT_FOUND", f"{resource}不存在或不可见", 404)
