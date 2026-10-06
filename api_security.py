"""Authentication is applied before JSON parsing, including unknown routes."""
import os
import secrets
from fastapi import Header, HTTPException
from starlette.responses import JSONResponse

MAX_BODY_BYTES = 8 * 1024 * 1024

def check_key(value):
    expected = os.environ.get("MACHINE_API_KEY", "")
    if len(expected) < 32:
        raise HTTPException(503, "API authentication not configured")
    if not value or not secrets.compare_digest(value.encode(), expected.encode()):
        raise HTTPException(401, "Unauthorized")

def require_api_key(x_api_key: str = Header(default="")):
    check_key(x_api_key)

class AuthenticatedBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            check_key(headers.get(b"x-api-key", b"").decode("latin1"))
        except HTTPException as exc:
            return await JSONResponse({"detail": exc.detail}, exc.status_code)(scope, receive, send)
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid length"}, 400)(scope, receive, send)
        if length < 0 or length > MAX_BODY_BYTES:
            return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY_BYTES:
                return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        replayed = False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)
