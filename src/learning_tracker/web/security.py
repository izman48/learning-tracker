"""Security headers on every response, and the CSRF check on state-changing requests.

Starlette answers an unhandled exception from its outermost layer, outside this
middleware, so app.py's 500 handler adds the same headers itself.

CSRF: SameSite=Lax keeps the session cookie off most cross-site POSTs. As a second
layer that does not depend on browser cookie rules, a request with any method but
GET, HEAD or OPTIONS must
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
# Two years; Caddy (or the self-hoster's proxy) terminates TLS. No includeSubDomains
# and no preload, on purpose: an instance often runs on a subdomain it doesn't own the
# parent of, and HSTS on the parent's other hosts is not ours to set.
HSTS = "max-age=63072000"
# Deny by default: every method except these must pass the CSRF check.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def security_headers(public_origin: str, path: str) -> dict[str, str]:
    headers = dict(HEADERS)
    if public_origin.startswith("https://"):
        headers["strict-transport-security"] = HSTS
    if path.startswith("/api/"):
        headers["cache-control"] = "no-store"
    return headers


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, public_origin: str) -> None:
        self.app = app
        self.public_origin = public_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # Browsers send cookies on cross-site WebSocket handshakes and CORS does
            # not apply, so a handshake must carry our own Origin (CSWSH).
            if Headers(scope=scope).get("origin") != self.public_origin:
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = security_headers(self.public_origin, scope["path"])

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra.items():
                    headers[name] = value
            await send(message)

        refusal = self.csrf_refusal(scope)
        if refusal is not None:
            status, detail = refusal
            await self.reply(send_with_headers, status, detail)
            return
        await self.app(scope, receive, send_with_headers)

    def csrf_refusal(self, scope: Scope) -> tuple[int, str] | None:
        if scope["method"] in SAFE_METHODS:
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
