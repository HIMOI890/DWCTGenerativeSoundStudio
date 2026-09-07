from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode
import re

SIGNED_MEDIA_VERSION = "1"
SIGNED_MEDIA_SIGNATURE_KEY = "edmg_sig"
SIGNED_MEDIA_EXPIRES_KEY = "edmg_exp"
SIGNED_MEDIA_VERSION_KEY = "edmg_sig_v"
_SIGNED_MEDIA_RESERVED_KEYS = frozenset(
    {
        SIGNED_MEDIA_SIGNATURE_KEY,
        SIGNED_MEDIA_EXPIRES_KEY,
        SIGNED_MEDIA_VERSION_KEY,
    }
)
_PROJECT_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_MEDIA_ROUTE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/audio$",
            re.IGNORECASE,
        ),
        "audio",
    ),
    (
        re.compile(
            rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/file$",
            re.IGNORECASE,
        ),
        "file",
    ),
    (
        re.compile(
            rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/preview/frame$",
            re.IGNORECASE,
        ),
        "preview_frame",
    ),
    (
        re.compile(
            rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/preview/segment$",
            re.IGNORECASE,
        ),
        "preview_segment",
    ),
    (
        re.compile(
            rf"^/v1/projects/(?P<project_id>{_PROJECT_ID_PATTERN})/preview/diffusion_segment$",
            re.IGNORECASE,
        ),
        "preview_diffusion_segment",
    ),
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
    normalized = str(path or "").strip()
    for pattern, purpose in _MEDIA_ROUTE_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match:
            return MediaRequestMatch(
                project_id=str(match.group("project_id") or ""),
                purpose=purpose,
            )
    return None


def _canonical_media_method(method: str) -> str:
    value = str(method or "GET").strip().upper() or "GET"
    return "GET" if value in {"GET", "HEAD"} else value


def _stringify_query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _iter_query_pairs(query: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> list[tuple[str, str]]:
    if isinstance(query, Mapping):
        items = query.items()
    else:
        items = list(query)
    pairs: list[tuple[str, str]] = []
    for raw_key, raw_value in items:
        key = str(raw_key)
        if raw_value is None:
            continue
        if isinstance(raw_value, (list, tuple)):
            for item in raw_value:
                if item is None:
                    continue
                pairs.append((key, _stringify_query_value(item)))
            continue
        pairs.append((key, _stringify_query_value(raw_value)))
    return pairs


def _canonical_query_string(pairs: Iterable[tuple[str, str]]) -> str:
    filtered = [
        (str(key), str(value))
        for key, value in pairs
        if str(key) not in _SIGNED_MEDIA_RESERVED_KEYS
    ]
    filtered.sort(key=lambda item: (item[0], item[1]))
    return urlencode(filtered, doseq=True)


class MediaUrlSigner:
    def __init__(self, secret: bytes):
        self._secret = bytes(secret)

    def _signature_payload(
        self,
        *,
        method: str,
        path: str,
        query_pairs: Iterable[tuple[str, str]],
        project_id: str,
        purpose: str,
        expires_at: int,
    ) -> bytes:
        parts = [
            SIGNED_MEDIA_VERSION,
            _canonical_media_method(method),
            str(path or ""),
            _canonical_query_string(query_pairs),
            str(project_id or ""),
            str(purpose or ""),
            str(int(expires_at)),
        ]
        return "\n".join(parts).encode("utf-8")

    def build_signature(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, Any] | Iterable[tuple[str, Any]],
        project_id: str,
        purpose: str,
        expires_at: int,
    ) -> str:
        payload = self._signature_payload(
            method=method,
            path=path,
            query_pairs=_iter_query_pairs(query),
            project_id=project_id,
            purpose=purpose,
            expires_at=expires_at,
        )
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def issue_signed_path(
        self,
        *,
        path: str,
        query: Mapping[str, Any] | Iterable[tuple[str, Any]],
        project_id: str,
        purpose: str,
        ttl_s: int,
        base_url: str = "",
        now: int | None = None,
    ) -> tuple[str, int]:
        query_pairs = _iter_query_pairs(query)
        expires_at = int(now if now is not None else time.time()) + max(1, int(ttl_s))
        signature = self.build_signature(
            method="GET",
            path=path,
            query=query_pairs,
            project_id=project_id,
            purpose=purpose,
            expires_at=expires_at,
        )
        signed_pairs = [
            *query_pairs,
            (SIGNED_MEDIA_VERSION_KEY, SIGNED_MEDIA_VERSION),
            (SIGNED_MEDIA_EXPIRES_KEY, str(expires_at)),
            (SIGNED_MEDIA_SIGNATURE_KEY, signature),
        ]
        suffix = urlencode(signed_pairs, doseq=True)
        signed_path = f"{path}?{suffix}" if suffix else path
        prefix = str(base_url or "").rstrip("/")
        return (f"{prefix}{signed_path}" if prefix else signed_path), expires_at

    def validate_request(
        self,
        *,
        method: str,
        path: str,
        query_string: bytes | str,
        project_id: str,
        purpose: str,
        now: int | None = None,
    ) -> SignedMediaValidation:
        raw_query = (
            query_string.decode("latin-1", errors="ignore")
            if isinstance(query_string, bytes)
            else str(query_string or "")
        )
        pairs = parse_qsl(raw_query, keep_blank_values=True)
        reserved = {
            key: [value for query_key, value in pairs if query_key == key]
            for key in _SIGNED_MEDIA_RESERVED_KEYS
        }
        present = any(values for values in reserved.values())
        if not present:
            return SignedMediaValidation(ok=False, present=False)
        if any(len(values) != 1 for values in reserved.values()):
            return SignedMediaValidation(
                ok=False,
                present=True,
                code="MEDIA_URL_MALFORMED",
                message="Signed media URL is malformed.",
            )
        version = reserved[SIGNED_MEDIA_VERSION_KEY][0]
        expires_raw = reserved[SIGNED_MEDIA_EXPIRES_KEY][0]
        signature = reserved[SIGNED_MEDIA_SIGNATURE_KEY][0]
        if version != SIGNED_MEDIA_VERSION:
            return SignedMediaValidation(
                ok=False,
                present=True,
                code="MEDIA_URL_MALFORMED",
                message="Signed media URL version is not supported.",
            )
        try:
            expires_at = int(expires_raw)
        except ValueError:
            return SignedMediaValidation(
                ok=False,
                present=True,
                code="MEDIA_URL_MALFORMED",
                message="Signed media URL expiry is malformed.",
            )
        current = int(now if now is not None else time.time())
        if expires_at < current:
            return SignedMediaValidation(
                ok=False,
                present=True,
                code="MEDIA_URL_EXPIRED",
                message="Signed media URL expired.",
            )
        expected = self.build_signature(
            method=method,
            path=path,
            query=pairs,
            project_id=project_id,
            purpose=purpose,
            expires_at=expires_at,
        )
        if not hmac.compare_digest(signature, expected):
            return SignedMediaValidation(
                ok=False,
                present=True,
                code="MEDIA_URL_INVALID",
                message="Signed media URL is invalid.",
            )
        return SignedMediaValidation(ok=True, present=True)
