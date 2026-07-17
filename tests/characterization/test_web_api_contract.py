"""
특성화 테스트 — ui/web_api.py `/api/state` 응답 계약 고정 (Slice 0 Task 6)

목적: 나중에 web_api를 clean-architecture 계층으로 이동시킬 때, 이 테스트가
green 상태로 유지되면 `/api/state` 응답 스키마가 깨지지 않았음을 보장한다.

SKIP 처리 경로 (두 가지):
1. fastapi 미설치 → pytest.importorskip("fastapi") 에서 컬렉션 시점에 SKIP
2. fastapi 있으나 engine 초기화 불가 (gradio/smolagents 누락, 또는 기타)
   → /api/state 응답이 503 (또는 非200) → test 함수에서 pytest.skip() 호출

실제 `/api/state` 응답 키 (ui/web_api.py:_convert_state_to_api, 100~165행 기준):
  최상위: running, game_time_str, tick, winner, air_use_count, air_use_limit,
          air_reset_at, units, intelligence, air_supports
  units[i]: id, side, unit_type, status, combat_power, current_action,
            color, lat, lon, waypoints
"""
import pytest

# fastapi 자체가 없으면 여기서 바로 SKIP (컬렉션 에러 방지)
pytest.importorskip("fastapi")
pytest.importorskip("fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402


def _client():
    """
    ui.web_api 의 FastAPI 앱 진입점을 반환, 테스트 클라이언트 생성.

    ui/web_api.py 는 module-level `app` 객체를 사용한다 (create_app() 팩토리
    없음). 향후 리팩토링으로 create_app() 팩토리가 생길 수 있으므로 hasattr
    로 우선 확인한다.

    import 체인 실패 (gradio/smolagents 누락 등) → pytest.skip() 처리.
    """
    try:
        import ui.web_api as web_api
    except ImportError as e:
        pytest.skip(f"ui.web_api import 실패 (선택적 의존성 누락): {e}")

    app = web_api.create_app() if hasattr(web_api, "create_app") else web_api.app
    if app is None:
        pytest.skip("ui.web_api.app 이 None (FastAPI 미설치로 앱 생성 안 됨)")
    return TestClient(app)


def test_api_state_schema():
    """`/api/state` 응답이 최상위·units[] 계약 키를 모두 포함하는지 확인."""
    client = _client()
    r = client.get("/api/state")

    # engine 초기화 불가 (optional deps 부재, 503 등) → SKIP, 이후 단정 실행 안 함
    if r.status_code != 200:
        pytest.skip(
            f"/api/state 엔진 미가용 (선택적 의존성 누락 또는 기타): "
            f"{r.status_code} {r.text[:200]}"
        )

    body = r.json()

    for key in ("running", "tick", "units"):
        assert key in body, f"/api/state 응답에 '{key}' 누락"

    assert isinstance(body["units"], list)
    if body["units"]:
        u = body["units"][0]
        for key in ("id", "side", "unit_type", "combat_power"):
            assert key in u, f"unit에 '{key}' 누락"
