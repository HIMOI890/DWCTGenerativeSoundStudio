from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode

SIGNED_MEDIA_VERSION = "1"
SIGNED_MEDIA_SIGNATURE_KEY = "edmg_sig"
SIGNED_MEDIA_EXPIRES_KEY = "edmg_exp"
SIGNED_MEDIA_VERSION_KEY = "edmg_sig_v"
_SIGNED_MEDIA_RESERVED_KEYS = frozenset(
    {SIGNED_MEDIA_SIGNATURE_KEY, SIGNED_MEDIA_EXPIRES_KEY, SIGNED_MEDIA_VERSION_KEY}
)
_PROJECT_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_MEDIA_ROUTE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/audio$", re.I), "audio"),
    (re.compile(rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/file$", re.I), "file"),
    (re.compile(rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/preview/frame$", re.I), "preview_frame"),
    (re.compile(rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/preview/segment$", re.I), "preview_segment"),
    (re.compile(rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/preview/diffusion_segment$", re.I), "preview_diffusion_segment"),
)


@dataclass(frozen=True)
class MediaRequestMatch:
    project_id: str
    purpose: str


@dataclass(frozen=True)
class SignedMediaValidation:
    ok: bool
    present: bool = False
    code: str | None = None
    message: str | None = None


def match_project_media_request(path: str) -> MediaRequestMatch | None:
    for pattern, purpose in _MEDIA_ROUTE_PATTERNS:
        match = pattern.fullmatch(str(path or "").strip())
        if match:
            return MediaRequestMatch(str(match.group("project_id") or ""), purpose)
    return None


def _canonical_media_method(method: str) -> str:
    value = str(method or "GET").strip().upper() or "GET"
    return "GET" if value in {"GET", "HEAD"} else value


def _stringify_query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _iter_query_pairs(query: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> list[tuple[str, str]]:
    items = query.items() if isinstance(query, Mapping) else list(query)
    pairs: list[tuple[str, str]] = []
    for raw_key, raw_value in items:
        key = str(raw_key)
        if raw_value is None:
            continue
        values = raw_value if isinstance(raw_value, (list, tuple)) else (raw_value,)
        pairs.extend((key, _stringify_query_value(value)) for value in values if value is not None)
    return pairs


def _canonical_query_string(pairs: Iterable[tuple[str, str]]) -> str:
    filtered = [(str(k), str(v)) for k, v in pairs if str(k) not in _SIGNED_MEDIA_RESERVED_KEYS]
    filtered.sort(key=lambda item: (item[0], item[1]))
    return urlencode(filtered, doseq=True)


def _encode_signature(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class MediaUrlSigner:
    """Issue with one key and verify with that key plus optional rotation keys."""

    def __init__(self, secret: bytes, previous_secrets: Sequence[bytes] = ()):
        if not secret:
            raise ValueError("A media signing secret is required")
        self._secret = bytes(secret)
        self._verification_secrets = (self._secret, *(bytes(value) for value in previous_secrets if value))

    @staticmethod
    def _signature_payload(*, method: str, path: str, query_pairs: Iterable[tuple[str, str]], project_id: str, purpose: str, expires_at: int) -> bytes:
        return "\n".join(
            [SIGNED_MEDIA_VERSION, _canonical_media_method(method), str(path or ""),
             _canonical_query_string(query_pairs), str(project_id or ""), str(purpose or ""), str(int(expires_at))]
        ).encode("utf-8")

    def build_signature(self, *, method: str, path: str, query: Mapping[str, Any] | Iterable[tuple[str, Any]], project_id: str, purpose: str, expires_at: int) -> str:
        payload = self._signature_payload(method=method, path=path, query_pairs=_iter_query_pairs(query), project_id=project_id, purpose=purpose, expires_at=expires_at)
        return _encode_signature(hmac.new(self._secret, payload, hashlib.sha256).digest())

    def issue_signed_path(self, *, path: str, query: Mapping[str, Any] | Iterable[tuple[str, Any]], project_id: str, purpose: str, ttl_s: int, base_url: str = "", now: int | None = None) -> tuple[str, int]:
        query_pairs = _iter_query_pairs(query)
        if any(key in _SIGNED_MEDIA_RESERVED_KEYS for key, _ in query_pairs):
            raise ValueError("Media query uses a reserved signing parameter")
        expires_at = int(now if now is not None else time.time()) + int(ttl_s)
        signature = self.build_signature(method="GET", path=path, query=query_pairs, project_id=project_id, purpose=purpose, expires_at=expires_at)
        signed_pairs = [*query_pairs, (SIGNED_MEDIA_VERSION_KEY, SIGNED_MEDIA_VERSION), (SIGNED_MEDIA_EXPIRES_KEY, str(expires_at)), (SIGNED_MEDIA_SIGNATURE_KEY, signature)]
        signed_path = f"{path}?{urlencode(signed_pairs, doseq=True)}"
        prefix = str(base_url or "").rstrip("/")
        return (f"{prefix}{signed_path}" if prefix else signed_path), expires_at

    def validate_request(self, *, method: str, path: str, query_string: bytes | str, project_id: str, purpose: str, max_ttl_s: int = 3600, now: int | None = None) -> SignedMediaValidation:
        if str(method or "").upper() not in {"GET", "HEAD"}:
            return SignedMediaValidation(False, False)
        raw_query = query_string.decode("latin-1", errors="strict") if isinstance(query_string, bytes) else str(query_string or "")
        try:
            pairs = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=True, errors="strict")
        except (UnicodeError, ValueError):
            return SignedMediaValidation(False, True, "MEDIA_URL_MALFORMED", "Signed media URL is malformed.")
        reserved = {key: [v for k, v in pairs if k == key] for key in _SIGNED_MEDIA_RESERVED_KEYS}
        present = any(reserved.values())
        if not present:
            return SignedMediaValidation(False, False)
        if any(len(values) != 1 for values in reserved.values()):
            return SignedMediaValidation(False, True, "MEDIA_URL_MALFORMED", "Signed media URL is malformed.")
        if reserved[SIGNED_MEDIA_VERSION_KEY][0] != SIGNED_MEDIA_VERSION:
            return SignedMediaValidation(False, True, "MEDIA_URL_MALFORMED", "Signed media URL version is not supported.")
        expires_raw = reserved[SIGNED_MEDIA_EXPIRES_KEY][0]
        if len(expires_raw) > 12 or not expires_raw.isascii() or not expires_raw.isdecimal():
            return SignedMediaValidation(False, True, "MEDIA_URL_MALFORMED", "Signed media URL expiry is malformed.")
        expires_at = int(expires_raw)
        current = int(now if now is not None else time.time())
        if expires_at <= current:
            return SignedMediaValidation(False, True, "MEDIA_URL_EXPIRED", "Signed media URL expired.")
        if expires_at > current + int(max_ttl_s):
            return SignedMediaValidation(False, True, "MEDIA_URL_INVALID_TTL", "Signed media URL lifetime is invalid.")
        payload = self._signature_payload(method=method, path=path, query_pairs=pairs, project_id=project_id, purpose=purpose, expires_at=expires_at)
        supplied = reserved[SIGNED_MEDIA_SIGNATURE_KEY][0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied):
            return SignedMediaValidation(False, True, "MEDIA_URL_INVALID", "Signed media URL is invalid.")
        valid = any(hmac.compare_digest(supplied, _encode_signature(hmac.new(secret, payload, hashlib.sha256).digest())) for secret in self._verification_secrets)
        if not valid:
            return SignedMediaValidation(False, True, "MEDIA_URL_INVALID", "Signed media URL is invalid.")
        return SignedMediaValidation(True, True)
