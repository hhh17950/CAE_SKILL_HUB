from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestBodyLimit:
    """Bound actual received bytes too, including requests without Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # FastAPI preserves HTTPException during body parsing; generic
                    # exceptions are otherwise translated to an incorrect HTTP 400.
                    raise HTTPException(413, "请求体超过服务限制")
            return message

        await self.app(scope, limited_receive, send)
