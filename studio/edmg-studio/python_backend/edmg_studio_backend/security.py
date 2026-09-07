from __future__ import annotations

import hmac
import hashlib
import secrets
import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Any

from starlette.responses import JSONResponse
from .media_signing import MediaUrlSigner, match_project_media_request

_PROCESS_MEDIA_KEY = secrets.token_bytes(32)


def media_url_ttl_s() -> int:
    try:
        value = int(os.getenv("EDMG_MEDIA_URL_TTL_S", "900"))
    except ValueError as exc:
        raise ValueError("EDMG_MEDIA_URL_TTL_S must be an integer from 60 to 3600") from exc
    if not 60 <= value <= 3600:
        raise ValueError("EDMG_MEDIA_URL_TTL_S must be between 60 and 3600")
    return value


def configured_media_signer(token: str) -> MediaUrlSigner:
    explicit = os.getenv("EDMG_MEDIA_SIGNING_SECRET", "")
    key = explicit.encode() if explicit else (
        hmac.new(token.encode(), b"edmg/studio/media-url/v1", hashlib.sha256).digest()
        if token else _PROCESS_MEDIA_KEY
    )
    previous = os.getenv("EDMG_MEDIA_SIGNING_SECRET_PREVIOUS", "")
    return MediaUrlSigner(key, [previous.encode()] if previous else [])


_TRUTHY = {"1", "true", "yes", "on"}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_PUBLIC_PATHS = frozenset({"/health", "/v1/security/status"})
_PROJECT_MEDIA_PATH = re.compile(
    r"^/v1/projects/[0-9a-f]{32}/(?:file|audio|preview(?:/.*)?)$",
    re.IGNORECASE,
)
# Electron loadFile / file:// sends Origin "null". Loopback Studio UI origins
# (any port) are covered by _LOCAL_DEV_CORS_ORIGIN_REGEX — do not pin Vite ports.
_DEFAULT_CORS_ORIGINS = ("null",)

# Always keep loopback Studio UI origins working even when cloud deploy
# scripts set EDMG_BACKEND_CORS_ORIGINS / EDMG_BACKEND_CORS_ORIGIN_REGEX.
_LOCAL_DEV_CORS_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip().rstrip("/") for part in str(value or "").split(",") if part.strip())


def _merge_cors_origins(env_value: str | None) -> tuple[str, ...]:
    """Union env allowlist with local defaults (file:// null + loopback via regex)."""
    merged: list[str] = list(_DEFAULT_CORS_ORIGINS)
    seen = set(merged)
    for origin in _split_csv(env_value):
        if origin not in seen:
            merged.append(origin)
            seen.add(origin)
    return tuple(merged)


def _merge_cors_origin_regex(env_value: str | None) -> str:
    """Keep local-dev regex active; OR in any configured cloud regex."""
    configured = str(env_value or "").strip()
    if not configured:
        return _LOCAL_DEV_CORS_ORIGIN_REGEX
    if configured == _LOCAL_DEV_CORS_ORIGIN_REGEX:
        return configured
    return f"(?:{_LOCAL_DEV_CORS_ORIGIN_REGEX})|(?:{configured})"


def is_loopback_host(host: str | None) -> bool:
    return str(host or "").strip().lower().strip("[]") in _LOOPBACK_HOSTS


def configured_backend_token() -> str:
    return (
        os.getenv("EDMG_BACKEND_AUTH_TOKEN", "").strip()
        or os.getenv("EDMG_STUDIO_BACKEND_AUTH_TOKEN", "").strip()
    )


@dataclass(frozen=True)
class BackendSecuritySettings:
    auth_mode: str
    auth_token: str
    configured_host: str
    allow_insecure_remote: bool
    cors_origins: tuple[str, ...]
    cors_origin_regex: str
    public_media_gets: bool

    @classmethod
    def from_env(cls) -> "BackendSecuritySettings":
        mode = os.getenv("EDMG_BACKEND_AUTH_MODE", "auto").strip().lower()
        if mode not in {"auto", "required", "disabled"}:
            mode = "auto"
        origins = _merge_cors_origins(os.getenv("EDMG_BACKEND_CORS_ORIGINS"))
        regex = _merge_cors_origin_regex(os.getenv("EDMG_BACKEND_CORS_ORIGIN_REGEX"))
        return cls(
            auth_mode=mode,
            auth_token=configured_backend_token(),
            configured_host=os.getenv("EDMG_STUDIO_BACKEND_HOST", "").strip(),
            allow_insecure_remote=_truthy(os.getenv("EDMG_BACKEND_ALLOW_INSECURE_REMOTE")),
            cors_origins=origins,
            cors_origin_regex=regex,
            public_media_gets=not str(
                os.getenv("EDMG_BACKEND_PUBLIC_MEDIA_GETS", "0")
            ).strip().lower()
            in {"0", "false", "no", "off"},
        )

    @property
    def auth_required(self) -> bool:
        if self.auth_mode == "disabled":
            return False
        if self.auth_mode == "required":
            return True
        return bool(self.auth_token)

    @property
    def remote_without_auth(self) -> bool:
        return self.remote_without_auth_for_server(None)

    def remote_without_auth_for_server(self, server_host: str | None) -> bool:
        if self.auth_required or self.allow_insecure_remote:
            return False
        if self.configured_host:
            return not is_loopback_host(self.configured_host)
        if self.auth_mode == "disabled" or not server_host:
            return False
        if is_loopback_host(server_host):
            return False
        try:
            ipaddress.ip_address(str(server_host).strip().strip("[]"))
        except ValueError:
            return False
        return True

    def public_status(
        self,
        *,
        request_scheme: str = "",
        request_server_host: str | None = None,
    ) -> dict[str, Any]:
        from dataclasses import asdict
        from .preview_limits import PreviewBudgetLimits
        scheme = str(request_scheme or "").strip().lower()
        remote_without_auth = self.remote_without_auth_for_server(request_server_host)
        return {
            "ok": True,
            "auth_mode": self.auth_mode,
            "auth_required": self.auth_required,
            "auth_configured": bool(self.auth_token),
            "configured_host": self.configured_host or None,
            "remote_without_auth": remote_without_auth,
            "allow_insecure_remote": self.allow_insecure_remote,
            "cors_origins": list(self.cors_origins),
            "cors_origin_regex_configured": bool(self.cors_origin_regex),
            "public_media_gets": self.public_media_gets,
            "media_url_ttl_s": media_url_ttl_s(),
            "preview_limits": {
                "standard": asdict(PreviewBudgetLimits.from_env()),
                "diffusion": asdict(PreviewBudgetLimits.from_env(diffusion=True)),
            },
            "transport": scheme or "unknown",
            "transport_secure": scheme == "https",
            "note": (
                "Bearer authentication is required for Studio control and metadata routes."
                if self.auth_required
                else "Loopback-compatible mode is active. Configure EDMG_BACKEND_AUTH_TOKEN for remote access."
            ),
        }


def validate_remote_bind_security(
    host: str,
    *,
    settings: BackendSecuritySettings | None = None,
) -> None:
    resolved = settings or BackendSecuritySettings.from_env()
    if is_loopback_host(host) or resolved.auth_required or resolved.allow_insecure_remote:
        return
    raise RuntimeError(
        "Refusing to bind the Studio backend to a non-loopback host without authentication. "
        "Set EDMG_BACKEND_AUTH_TOKEN and EDMG_BACKEND_AUTH_MODE=required. For an explicitly "
        "isolated development network only, EDMG_BACKEND_ALLOW_INSECURE_REMOTE=1 bypasses this guard."
    )


def _is_public_request(path: str, method: str, settings: BackendSecuritySettings) -> bool:
    if method == "OPTIONS" or path in _PUBLIC_PATHS:
        return True
    return bool(
        settings.public_media_gets
        and method in {"GET", "HEAD"}
        and _PROJECT_MEDIA_PATH.fullmatch(path)
    )


def _bearer_token(headers: list[tuple[bytes, bytes]]) -> str:
    for raw_name, raw_value in headers:
        if raw_name.lower() != b"authorization":
            continue
        value = raw_value.decode("latin-1").strip()
        scheme, _, token = value.partition(" ")
        return token.strip() if scheme.lower() == "bearer" else ""
    return ""


class BackendSecurityMiddleware:
    """Enforce backend bearer auth and add baseline security headers centrally."""

    def __init__(self, app: Any, *, settings: BackendSecuritySettings) -> None:
        self.app = app
        self.settings = settings
        self.media_signer = configured_media_signer(settings.auth_token)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        media = match_project_media_request(path)
        signed = self.media_signer.validate_request(
            method=method, path=path, query_string=scope.get("query_string", b""),
            project_id=media.project_id, purpose=media.purpose,
        ) if media and method in {"GET", "HEAD"} else None

        async def send_with_security_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {name.lower() for name, _value in headers}
                additions = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                ]
                additions.append((b"cache-control", b"no-store"))
                if signed and signed.present:
                    headers = [(name, value) for name, value in headers if name.lower() != b"cache-control"]
                    existing.discard(b"cache-control")
                for name, value in additions:
                    if name not in existing:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        server = scope.get("server")
        server_host = str(server[0]) if isinstance(server, (list, tuple)) and server else None
        if self.settings.remote_without_auth_for_server(server_host) and not _is_public_request(path, method, self.settings):
            response = JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "error": {
                        "message": "Remote backend authentication is not configured.",
                        "hint": "Set EDMG_BACKEND_AUTH_TOKEN and EDMG_BACKEND_AUTH_MODE=required, then restart the backend.",
                        "code": "BACKEND_AUTH_CONFIGURATION_REQUIRED",
                    },
                },
            )
            await response(scope, receive, send_with_security_headers)
            return

        if signed and signed.present and not signed.ok:
            response = JSONResponse(status_code=401, content={"ok": False, "error": {
                "code": signed.code, "message": signed.message,
            }})
            await response(scope, receive, send_with_security_headers)
            return

        if self.settings.auth_required and not _is_public_request(path, method, self.settings) and not (signed and signed.ok):
            supplied = _bearer_token(list(scope.get("headers") or []))
            if not supplied or not hmac.compare_digest(supplied, self.settings.auth_token):
                response = JSONResponse(
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                    content={
                        "ok": False,
                        "error": {
                            "message": "Backend authentication required.",
                            "hint": "Open Studio Settings -> Desktop Backend and save the matching backend access token.",
                            "code": "BACKEND_AUTH_REQUIRED",
                        },
                    },
                )
                await response(scope, receive, send_with_security_headers)
                return

        await self.app(scope, receive, send_with_security_headers)
