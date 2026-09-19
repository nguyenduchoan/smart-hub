"""Security middleware, host validation, origin verification, and CSRF token management for local dashboard."""
import ipaddress
import secrets
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
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

# Token lifecycle store: token -> expiry timestamp
_ACTIVE_CSRF_TOKENS: Dict[str, float] = {}


def issue_csrf_token(ttl_seconds: int = 86400) -> str:
    """Issue a new time-bound CSRF token."""
    token = secrets.token_hex(24)
    _ACTIVE_CSRF_TOKENS[token] = time.time() + ttl_seconds
    return token


def validate_csrf_token(token: Optional[str]) -> Tuple[bool, str]:
    """Validate CSRF token against active lifecycle store and startup baseline."""
    if not token or not str(token).strip():
        return False, "CSRF token missing or invalid."

    # Baseline constant token support (for dev/test compatibility)
    if secrets.compare_digest(token, CSRF_TOKEN):
        return True, "OK"

    now = time.time()
    for tok, expiry in list(_ACTIVE_CSRF_TOKENS.items()):
        if secrets.compare_digest(token, tok):
            if now > expiry:
                _ACTIVE_CSRF_TOKENS.pop(tok, None)
                return False, "CSRF token has expired."
            return True, "OK"

    return False, "CSRF token missing or invalid."


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
    """Restricts access to local loopback hosts and verifies origin and CSRF on mutation requests."""

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
                parsed_origin = urlparse(origin)
                origin_host = extract_hostname(origin)
                if not is_allowed_host(origin_host, self.allowed_hosts):
                    return Response(
                        content=f"Origin '{origin}' is forbidden.",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )

                # V2-16: Strict scheme and port matching
                # Skip port matching only if Host is literal testserver without port
                parsed_host = urlparse(f"//{host_header}") if host_header else None
                expected_port = None
                if parsed_host and parsed_host.port is not None:
                    expected_port = parsed_host.port
                elif request.url.port is not None:
                    expected_port = request.url.port

                origin_port = parsed_origin.port
                if origin_port is None and parsed_origin.scheme:
                    origin_port = 443 if parsed_origin.scheme.lower() == "https" else 80

                if expected_port is not None and origin_port is not None:
                    if origin_port != expected_port:
                        return Response(
                            content=f"Origin '{origin}' port ({origin_port}) does not match server port ({expected_port}).",
                            status_code=status.HTTP_403_FORBIDDEN,
                        )

                # Scheme matching
                expected_scheme = request.url.scheme or "http"
                if parsed_origin.scheme and parsed_origin.scheme.lower() != expected_scheme.lower():
                    return Response(
                        content=f"Origin '{origin}' scheme does not match server scheme ({expected_scheme}).",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )

            # 3. CSRF token validation
            # Exempt health and csrf-token endpoints
            path = request.url.path
            if path not in ("/api/health", "/api/csrf-token"):
                csrf_header = request.headers.get("x-csrf-token")
                is_valid, reason = validate_csrf_token(csrf_header)
                if not is_valid:
                    token_hdr = request.headers.get("x-session-token")
                    if not token_hdr or not secrets.compare_digest(token_hdr, SESSION_TOKEN):
                        return Response(
                            content=f"{reason} Please refresh the dashboard.",
                            status_code=status.HTTP_403_FORBIDDEN,
                        )

        response = await call_next(request)
        # Set security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
