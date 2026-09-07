"""Client fixture for route tests that are not testing stale-write handling.

Like Studio, load the current project revision before an interactive action.
Revision/conflict tests deliberately use the unmodified FastAPI TestClient.
"""
import re
from fastapi.testclient import TestClient as BaseTestClient


class TestClient(BaseTestClient):
    __test__ = False

    def request(self, method, url, **kwargs):
        match = re.search(r"(/v1/projects/[^/?]+)", str(url))
        if match and method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
            headers = dict(kwargs.get("headers") or {})
            body = kwargs.get("json") or {}
            if "If-Match" not in headers and "expected_revision" not in body:
                response = super().request("GET", match[1], headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    revision = (data.get("project") or data).get("revision")
                    if revision is not None:
                        headers["If-Match"] = str(revision)
                        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)
