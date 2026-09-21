import hmac
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.errors import DomainError

bearer = HTTPBearer(auto_error=False)


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
