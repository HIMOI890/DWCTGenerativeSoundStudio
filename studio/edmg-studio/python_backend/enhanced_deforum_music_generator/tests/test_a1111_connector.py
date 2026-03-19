import pytest
from config.config_system import A1111Config
from integrations.a1111_connector import A1111Connector


def test_a1111_ping(monkeypatch):
    cfg = A1111Config(host="127.0.0.1", port=7860)
    conn = A1111Connector(cfg)

    # Monkeypatch _get to avoid real HTTP
    monkeypatch.setattr(conn, "_get", lambda path, params=None: {"progress": 0.5})

    assert conn.ping() is True


def test_txt2img_builds_payload(monkeypatch):
    cfg = A1111Config(host="127.0.0.1", port=7860)
    conn = A1111Connector(cfg)

    fake_response = {"images": ["dGVzdA=="], "parameters": {"steps": 5}, "info": "{}"}
    monkeypatch.setattr(conn, "_post", lambda path, payload: fake_response)

    res = conn.txt2img("hello world", steps=5, width=128, height=128)
    assert len(res.images_b64) == 1
