"""Task 30 — web_api → c2.presentation.web.api 컨테이너 세션 배선 검증.

목적:
  1. `src/c2/presentation/web/api.py` 소스가 더 이상 `ui.gradio_app`을 참조하지 않음
     (`_ga(` 헬퍼도 완전히 제거됨)을 소스 검사로 고정한다.
  2. fastapi가 설치된 이 환경에서는 실제 TestClient로 `/api/state`,
     `/api/control/start`, `/api/auto_plan_status`가 정상 200 응답(503/500 아님)임을
     런타임으로 검증한다 — gradio 없이도 컨테이너(`c2.composition.container.build_session`)
     경유로 엔진이 뜨는지 보장.
  3. `ui/web_api.py`는 shim(re-export)이며 자체적으로 gradio_app을 import하지 않음.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_API_SRC = _ROOT / "src" / "c2" / "presentation" / "web" / "api.py"
_SHIM = _ROOT / "ui" / "web_api.py"


def test_api_source_has_no_gradio_reference():
    """api.py 소스에 gradio_app import / `_ga(` 헬퍼가 없어야 한다.

    (역사적 맥락을 설명하는 docstring 언급은 허용 — 실제 import/호출 구문만 금지.)
    """
    assert _API_SRC.exists(), f"{_API_SRC} 가 존재하지 않습니다 (아직 이식 전)"
    src = _API_SRC.read_text(encoding="utf-8")
    assert "import ui.gradio_app" not in src, "api.py가 여전히 gradio_app을 import합니다"
    assert "from ui.gradio_app" not in src, "api.py가 여전히 gradio_app을 import합니다"
    assert "_ga(" not in src, "api.py가 여전히 _ga() 헬퍼를 사용합니다"
    assert "import gradio" not in src


def test_shim_has_no_gradio_reference():
    """ui/web_api.py shim도 gradio_app을 직접 import하지 않아야 한다."""
    assert _SHIM.exists()
    src = _SHIM.read_text(encoding="utf-8")
    assert "import ui.gradio_app" not in src
    assert "from ui.gradio_app" not in src
    assert "from ui import gradio_app" not in src


pytest.importorskip("fastapi")
pytest.importorskip("fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402


def _client() -> TestClient:
    from c2.presentation.web.api import create_app

    return TestClient(create_app())


def test_api_state_schema_via_container_session():
    """`/api/state`가 gradio 없이(container 세션 경유) 200 + 계약 스키마를 반환해야 한다."""
    client = _client()
    r = client.get("/api/state")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("running", "tick", "units"):
        assert key in body
    assert isinstance(body["units"], list)
    if body["units"]:
        u = body["units"][0]
        for key in ("id", "side", "unit_type", "combat_power"):
            assert key in u


def test_control_start_smoke():
    client = _client()
    r = client.post("/api/control/start")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "running" in body
    # 원복 (테스트 프로세스 내 세션이 계속 실행 상태로 남지 않도록)
    if body.get("running"):
        client.post("/api/control/stop")


def test_auto_plan_status_smoke():
    client = _client()
    r = client.get("/api/auto_plan_status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "active" in body
    assert "message" in body
