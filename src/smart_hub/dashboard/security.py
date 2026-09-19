"""Security middleware, host validation, and CSRF token management for local dashboard."""
import secrets
from typing import List, Set
from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

# Generate a cryptographically strong session token on startup
SESSION_TOKEN = secrets.token_urlsafe(32)
CSRF_TOKEN = secrets.token_hex(24)

DEFAULT_ALLOWED_HOSTS: Set[str] = {
    "127.0.0.1",
    "localhost",
    "::1",
    "testserver",
}


class HostOriginSecurityMiddleware(BaseHTTPMiddleware):
    """Restricts access to local loopback hosts and verifies CSRF on mutation requests."""

    def __init__(self, app, allowed_hosts: Set[str] | None = None):
        super().__init__(app)
        self.allowed_hosts = set(allowed_hosts) if allowed_hosts else set(DEFAULT_ALLOWED_HOSTS)

    def add_allowed_host(self, host: str):
        self.allowed_hosts.add(host)

    async def dispatch(self, request: Request, call_next):
        # 1. Host validation
        host_header = request.headers.get("host", "").split(":")[0].strip()
        if host_header and host_header not in self.allowed_hosts and not host_header.startswith("127."):
            return Response(
                content=f"Host '{host_header}' is not allowed. Dashboard is strictly bound to local loopback.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        # 2. Origin validation on state-changing requests
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin:
                origin_host = origin.split("://")[-1].split(":")[0]
                if origin_host not in self.allowed_hosts and not origin_host.startswith("127."):
                    return Response(
                        content=f"Origin '{origin}' is forbidden.",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )

            # 3. CSRF token validation
            # Exempt health and csrf-token endpoints
            path = request.url.path
            if path not in ("/api/health", "/api/csrf-token"):
                csrf_header = request.headers.get("x-csrf-token")
                # Also accept cookie or header
                if not csrf_header or not secrets.compare_digest(csrf_header, CSRF_TOKEN):
                    # For convenient local testing without browser session, accept session token header if matching
                    token_hdr = request.headers.get("x-session-token")
                    if not token_hdr or not secrets.compare_digest(token_hdr, SESSION_TOKEN):
                        return Response(
                            content="CSRF token missing or invalid. Please refresh the dashboard.",
                            status_code=status.HTTP_403_FORBIDDEN,
                        )

        response = await call_next(request)
        # Set security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
