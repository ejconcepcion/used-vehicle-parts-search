"""Forms-based authentication middleware.

Protects all routes with a session cookie.  Login/logout are at /login and
/logout.  API routes return 401 JSON when unauthenticated; everything else
redirects to /login.

Disabled (pass-through) when AUTH_USERNAME or AUTH_PASSWORD is not set.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import config

_EXEMPT = {"/login", "/logout"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Auth is disabled if no credentials are configured
        if not config.AUTH_USERNAME or not config.AUTH_PASSWORD:
            return await call_next(request)

        path = request.url.path
        if path in _EXEMPT:
            return await call_next(request)

        if not request.session.get("authenticated"):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(f"/login?next={path}", status_code=302)

        return await call_next(request)
