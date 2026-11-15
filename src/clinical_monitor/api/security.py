import secrets

from fastapi import Request
from fastapi.responses import JSONResponse


async def authenticate(request: Request, call_next):
    token = request.app.state.settings.api_token
    if token is not None and token.get_secret_value() and request.url.path.startswith("/v1"):
        header = request.headers.get("Authorization", "")
        if not secrets.compare_digest(header, f"Bearer {token.get_secret_value()}"):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)
