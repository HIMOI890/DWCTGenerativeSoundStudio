import asyncio

import httpx

from src.api.main import app


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


def test_health():
    response = asyncio.run(_get("/health/"))
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status():
    response = asyncio.run(_get("/status/"))
    assert response.status_code == 200
    assert "service" in response.json()
