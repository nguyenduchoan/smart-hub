"""Security middleware, host validation, and CSRF token management for local dashboard."""
import ipaddress
import secrets
from typing import List, Optional, Set
from urllib.parse import splitport, urlparse
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


def extract_hostname(netloc_or_host: str) -> str:
    """Extract clean hostname from Host header or netloc, handling IPv6 brackets and ports."""
    val = netloc_or_host.strip()
    if not val:
        return ""
    parsed = urlparse(val if "://" in val else f"//{val}")
    return (parsed.hostname or val).strip()


def is_allowed_host(hostname: str, allowed_hosts: Set[str]) -> bool:
    """Validate if hostname is explicitly allowed or a legitimate loopback IP."""
    if not hostname:
        return False
    hostname_clean = hostname.lower().strip()
    if hostname_clean in allowed_hosts:
        return True

    # Check if it is a valid IP address and is loopback
    try:
        ip = ipaddress.ip_address(hostname_clean)
        return ip.is_loopback
    except ValueError:
        # Domain name that is not in allowed_hosts (e.g. 127.evil.test)
        return False


class HostOriginSecurityMiddleware(BaseHTTPMiddleware):
    """Restricts access to local loopback hosts and verifies CSRF on mutation requests."""

    def __init__(self, app, allowed_hosts: Set[str] | None = None):
        super().__init__(app)
        self.allowed_hosts = {h.lower().strip() for h in (allowed_hosts or DEFAULT_ALLOWED_HOSTS)}

    def add_allowed_host(self, host: str):
        self.allowed_hosts.add(host.lower().strip())

    async def dispatch(self, request: Request, call_next):
        # 1. Host validation
        host_header = request.headers.get("host", "")
        if host_header:
            hostname = extract_hostname(host_header)
            if not is_allowed_host(hostname, self.allowed_hosts):
                return Response(
                    content=f"Host '{host_header}' is not allowed. Dashboard is strictly bound to local loopback.",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

        # 2. Origin validation on state-changing requests
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin:
                origin_host = extract_hostname(origin)
                if not is_allowed_host(origin_host, self.allowed_hosts):
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
