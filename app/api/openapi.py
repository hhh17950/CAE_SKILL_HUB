from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def install_openapi(app: FastAPI):
    """Document the actual RFC 9457 error media type, without a misleading JSON alternative."""

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title, version=app.version, description=app.description, routes=app.routes
            )
            for path in schema["paths"].values():
                for operation in path.values():
                    if not isinstance(operation, dict):
                        continue
                    for code, response in operation.get("responses", {}).items():
                        if code.isdigit() and int(code) >= 400:
                            response["content"] = {
                                "application/problem+json": {
                                    "schema": {"$ref": "#/components/schemas/Problem"}
                                }
                            }
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
