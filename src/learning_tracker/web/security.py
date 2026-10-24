"""Security headers on every response, and the CSRF check on state-changing requests.

CSRF: SameSite=Lax keeps the session cookie off most cross-site POSTs. As a second
layer that does not depend on browser cookie rules, an unsafe request must
- carry an Origin header equal to LT_PUBLIC_ORIGIN (missing is refused),
- not be marked cross-site or same-site by Sec-Fetch-Site, when the browser sends it,
- send JSON, which a plain HTML form cannot.

Written as plain ASGI so it wraps every response, including errors and 404s.
"""

import json

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'; manifest-src 'self'; worker-src 'self'"
)
PERMISSIONS = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
    "accelerometer=(), gyroscope=(), magnetometer=(), display-capture=()"
)
HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": PERMISSIONS,
}
HSTS = "max-age=63072000"  # two years; Caddy (or the self-hoster's proxy) terminates TLS
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, public_origin: str) -> None:
        self.app = app
        self.public_origin = public_origin
        self.headers = dict(HEADERS)
        if public_origin.startswith("https://"):
            self.headers["strict-transport-security"] = HSTS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers[name] = value
                if is_api:
                    headers["cache-control"] = "no-store"
            await send(message)

        refusal = self.csrf_refusal(scope)
        if refusal is not None:
            status, detail = refusal
            await self.reply(send_with_headers, status, detail)
            return
        await self.app(scope, receive, send_with_headers)

    def csrf_refusal(self, scope: Scope) -> tuple[int, str] | None:
        if scope["method"] not in UNSAFE_METHODS:
            return None
        headers = Headers(scope=scope)
        if headers.get("origin") != self.public_origin:
            return 403, "Cross-origin request refused"
        if headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return 403, "Cross-origin request refused"
        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return 415, "Requests must be JSON"
        return None

    @staticmethod
    async def reply(send: Send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
