from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from edmg_studio_backend.api.media import create_media_router, validate_preview
from edmg_studio_backend.media_signing import MediaUrlSigner
from edmg_studio_backend.security import BackendSecurityMiddleware, BackendSecuritySettings
from edmg_studio_backend.store.projects import ProjectStore
from edmg_studio_backend.utils.path import safe_join


def test_authenticated_issuance_range_head_and_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv("EDMG_BACKEND_AUTH_MODE", "required")
    monkeypatch.setenv("EDMG_BACKEND_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("EDMG_BACKEND_PUBLIC_MEDIA_GETS", raising=False)
    store = ProjectStore(tmp_path)
    project = store.create("Signed media")
    media = store.project_dir(project.id) / "sample.wav"
    media.write_bytes(b"0123456789")
    settings = BackendSecuritySettings.from_env()
    app = FastAPI()
    app.include_router(create_media_router(lambda: store, settings))
    app.add_middleware(BackendSecurityMiddleware, settings=settings)

    @app.api_route("/v1/projects/{project_id}/file", methods=["GET", "HEAD"])
    def file(project_id: str, path: str):
        return FileResponse(safe_join(store.project_dir(project_id), path))

    with TestClient(app) as client:
        endpoint = f"/v1/projects/{project.id}/media-urls"
        payload = {"requests": [{"purpose": "file", "path": "sample.wav"}]}
        assert client.post(endpoint, json=payload).status_code == 401
        assert client.get(f"/v1/projects/{project.id}/file?path=sample.wav").status_code == 401
        response = client.post(endpoint, json=payload, headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 200, response.text
        url = response.json()["urls"][0]["url"]
        assert client.get(url).content == b"0123456789"
        assert client.head(url).content == b""
        ranged = client.get(url, headers={"Range": "bytes=2-4"})
        assert ranged.status_code == 206
        assert ranged.content == b"234"
        assert ranged.headers["cache-control"] == "no-store"
        assert client.get(url.replace("sample.wav", "other.wav")).status_code == 401


@pytest.mark.parametrize("signature", ["é", "☃", "", "x" * 5000])
def test_malformed_signature_never_raises(signature):
    signer = MediaUrlSigner(b"secret")
    url, _ = signer.issue_signed_path(path="/file", query={}, project_id="p", purpose="file", ttl_s=60, now=100)
    query = dict(parse_qsl(urlsplit(url).query))
    query["edmg_sig"] = signature
    assert not signer.validate_request(method="GET", path="/file", query_string=urlencode(query), project_id="p", purpose="file", now=101).ok
    query["edmg_exp"] = "9" * 5000
    assert not signer.validate_request(method="GET", path="/file", query_string=urlencode(query), project_id="p", purpose="file", now=101).ok


def test_rotation_and_expiration():
    old, rotated = MediaUrlSigner(b"old"), MediaUrlSigner(b"new", [b"old"])
    url, _ = old.issue_signed_path(path="/file", query={}, project_id="p", purpose="file", ttl_s=60, now=100)
    args = dict(method="HEAD", path="/file", query_string=urlsplit(url).query, project_id="p", purpose="file")
    assert rotated.validate_request(**args, now=101).ok
    assert not rotated.validate_request(**args, now=160).ok
    assert not MediaUrlSigner(b"new").validate_request(**args, now=101).ok


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:\\outside", "%2e%2e/outside", "%252e%252e/outside", "bad%zz"])
def test_media_path_rejections(tmp_path, path):
    with pytest.raises(ValueError):
        safe_join(tmp_path, path)


def test_preview_budgets_are_separate_and_finite():
    validate_preview("segment", {"w": 1024, "h": 1024, "fps": 12, "end_s": 10})
    validate_preview("diffusion_segment", {"w": 1024, "h": 1024, "fps": 8, "end_s": 5, "steps": 30})
    for kind, query in [("frame", {"w": 2049}), ("frame", {"t": float("nan")}),
                        ("segment", {"end_s": 11}), ("segment", {"fps": 13}),
                        ("diffusion_segment", {"steps": 31}), ("diffusion_segment", {"w": 1025}),
                        ("segment", {"w": 2048, "h": 2048, "fps": 12, "end_s": 10})]:
        with pytest.raises(ValueError):
            validate_preview(kind, query)
