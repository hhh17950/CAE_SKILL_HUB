import hmac
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.dependencies import Container
from app.errors import DomainError
from app.schemas.common import Response
from app.services.context import CallContext

bearer = HTTPBearer(auto_error=False)


def container(request: Request) -> Container:
    return request.app.state.container


def owner(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is not None:
        for token, identity in request.app.state.settings.api_tokens.items():
            if hmac.compare_digest(credentials.credentials.encode(), token.encode()):
                return identity
    raise DomainError("UNAUTHORIZED", "请提供有效 Bearer 测试令牌", 401)


Owner = Annotated[str, Depends(owner)]
ContainerDep = Annotated[Container, Depends(container)]


def call_context(
    identity: Owner,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    ],
) -> CallContext:
    return CallContext(identity, idempotency_key)


Call = Annotated[CallContext, Depends(call_context)]


def envelope(request: Request, data):
    return Response(request_id=request.state.request_id, data=data)
