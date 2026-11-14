"""ASGI middleware stack for the HTTP transports."""

from typing import Any

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from windows_mcp.infrastructure import AuthKeyMiddleware, IPAllowlistMiddleware, OAuthOnlyMiddleware


class OptionsMiddleware:
    """ASGI middleware that intercepts OPTIONS preflight requests.

    Only echoes CORS headers when the request Origin is in the explicit allowlist.
    With an empty allowlist (default), returns 200 OK with no CORS headers so that
    browsers block cross-origin access via Same-Origin Policy.
    """

    def __init__(self, app: Any, *, allowed_origins: list[str] | None = None) -> None:
        self.app = app
        self._allowed: frozenset[str] = frozenset(allowed_origins or [])

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] == "OPTIONS":
            headers: list[list[bytes]] = [[b"content-length", b"0"]]
            if self._allowed:
                origin = next(
                    (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"origin"),
                    None,
                )
                if origin and origin in self._allowed:
                    headers += [
                        [b"access-control-allow-origin", origin.encode("latin-1")],
                        [b"access-control-allow-methods", b"GET, POST, OPTIONS"],
                        [
                            b"access-control-allow-headers",
                            b"content-type, authorization, mcp-session-id",
                        ],
                        [b"vary", b"Origin"],
                    ]
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
        else:
            await self.app(scope, receive, send)




def http_middleware(
    auth_key: str | None = None,
    ip_allowlist: list | None = None,
    oauth_validator=None,
    cors_origins: list[str] | None = None,
    allowed_hosts: list[str] | None = None,
) -> list:
    """Return ASGI middleware for HTTP transports."""
    middleware: list = [
        Middleware(OptionsMiddleware, allowed_origins=cors_origins or []),
    ]
    if allowed_hosts:
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        middleware.append(Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts))
    if cors_origins:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=cors_origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization", "Mcp-Session-Id"],
                allow_credentials=False,
            )
        )
    if ip_allowlist:
        middleware.append(Middleware(IPAllowlistMiddleware, allowlist=ip_allowlist))
    if auth_key:
        middleware.append(
            Middleware(AuthKeyMiddleware, auth_key=auth_key, oauth_validator=oauth_validator)
        )
    elif oauth_validator:
        middleware.append(Middleware(OAuthOnlyMiddleware, oauth_validator=oauth_validator))
    return middleware
