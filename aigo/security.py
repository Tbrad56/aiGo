"""Keep aiGo's local server private to aiGo's own page.

The server only listens on 127.0.0.1, but any website open in the browser can still send requests
to it. This middleware blocks the three ways a site could abuse that:

* DNS rebinding: a site points its own domain at 127.0.0.1 to become "same-origin" with aiGo.
  Blocked by accepting only requests addressed to 127.0.0.1 / localhost (the Host header).
* Cross-site requests (CSRF): a page sends POST /api/clear etc. blind. Blocked by refusing any
  state-changing request whose Origin isn't aiGo's own page.
* Cross-site WebSocket hijacking: browsers don't apply CORS to WebSockets, so a page could stream
  /ws. Blocked by the same Origin check on the WebSocket handshake.

Requests without an Origin header (curl, the tray menu, tests) are allowed: browsers always send
Origin on cross-site POSTs and on WebSocket handshakes, so a web page can't omit it.
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _split_host(value: str) -> tuple[str, Optional[str]]:
    """'127.0.0.1:8765' -> ('127.0.0.1', '8765'); '[::1]:8765' -> ('[::1]', '8765')."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        host, rest = value[: end + 1], value[end + 1 :]
        return host, rest[1:] if rest.startswith(":") else None
    host, _, port = value.partition(":")
    return host, port or None


def host_allowed(host_header: Optional[str]) -> bool:
    if not host_header:
        return False
    host, _ = _split_host(host_header)
    return host in ALLOWED_HOSTS


def origin_allowed(origin: Optional[str], host_header: str) -> bool:
    """No Origin (non-browser client) is fine; otherwise it must be aiGo's own page."""
    if origin is None:
        return True
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme != "http" or not parts.hostname:
        return False  # includes the literal "null" origin of sandboxed frames and file:// pages
    origin_host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    _, port = _split_host(host_header)
    return origin_host in ALLOWED_HOSTS and str(parts.port or 80) == (port or "80")


class LocalOnlyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        origin = headers.get("origin")

        if not host_allowed(host):
            return await self._refuse(scope, send, f"Host not allowed: {host!r}")
        checks_origin = scope["type"] == "websocket" or scope.get("method", "GET") not in SAFE_METHODS
        if checks_origin and not origin_allowed(origin, host):
            return await self._refuse(scope, send, f"Origin not allowed: {origin!r}")
        return await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope, send, why: str) -> None:
        log.warning("refused %s %s: %s", scope["type"], scope.get("path"), why)
        if scope["type"] == "websocket":
            # Closing before accepting makes the server answer the handshake with HTTP 403.
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": "This request isn't allowed. Open aiGo from http://127.0.0.1."}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
