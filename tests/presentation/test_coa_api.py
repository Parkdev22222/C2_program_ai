"""COA API 계약."""
from fastapi.testclient import TestClient
from c2.presentation.web.api import create_app


def _client():
    return TestClient(create_app())


def test_coa_execute_endpoint_exists_and_validates():
    c = _client()
    # pending 없을 때 실행 → ok False (404 아님)
    r = c.post("/api/mission/coa/execute", json={"index": 0})
    assert r.status_code == 200
    assert r.json().get("ok") is False


def test_attack_returns_job():
    c = _client()
    r = c.post("/api/mission/attack")
    assert r.status_code == 200
    assert "job_id" in r.json()
