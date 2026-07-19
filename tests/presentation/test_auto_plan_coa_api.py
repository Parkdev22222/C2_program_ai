"""auto_plan_status API가 coas/coa_gen_id를 노출."""
from fastapi.testclient import TestClient
from c2.presentation.web.api import create_app


def test_auto_plan_status_has_coa_fields():
    c = TestClient(create_app())
    c.get("/api/state")   # 엔진 확보
    r = c.get("/api/auto_plan_status")
    assert r.status_code == 200
    d = r.json()
    assert "coas" in d and "coa_gen_id" in d
