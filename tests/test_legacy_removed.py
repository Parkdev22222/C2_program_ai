"""
레거시 top-level 패키지 삭제 검증 (Task 34, Slice 5)

목적: Task 33에서 모든 소비자가 c2.* import로 이전을 완료했으므로,
레거시 shim 패키지(wargame/agent/ontology/tools, ui/__init__.py, ui/web_api.py)를
삭제해도 아무것도 깨지지 않음을 보장한다.

ui/dashboard/ (FastAPI web_api가 서빙하는 정적 HTML 대시보드)는 Python 패키지가
아니라 정적 자산 디렉터리이므로 삭제 대상이 아니다 — 계속 존재해야 한다.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_wargame_dir_removed():
    assert not (REPO_ROOT / "wargame").exists(), "wargame/ 레거시 shim 패키지가 여전히 존재합니다"


def test_legacy_agent_dir_removed():
    assert not (REPO_ROOT / "agent").exists(), "agent/ 레거시 shim 패키지가 여전히 존재합니다"


def test_legacy_ontology_dir_removed():
    assert not (REPO_ROOT / "ontology").exists(), "ontology/ 레거시 shim 패키지가 여전히 존재합니다"


def test_legacy_tools_dir_removed():
    assert not (REPO_ROOT / "tools").exists(), "tools/ 레거시 shim 패키지가 여전히 존재합니다"


def test_legacy_ui_init_removed():
    assert not (REPO_ROOT / "ui" / "__init__.py").exists(), "ui/__init__.py shim이 여전히 존재합니다"


def test_legacy_ui_web_api_removed():
    assert not (REPO_ROOT / "ui" / "web_api.py").exists(), "ui/web_api.py shim이 여전히 존재합니다"


def test_ui_dashboard_preserved():
    dashboard_dir = REPO_ROOT / "ui" / "dashboard"
    assert dashboard_dir.exists(), "ui/dashboard/ 는 삭제 대상이 아닙니다 (FastAPI가 서빙하는 정적 대시보드)"
    assert (dashboard_dir / "index.html").exists(), "ui/dashboard/index.html 이 존재해야 합니다"


def test_c2_web_api_importable():
    import c2.presentation.web.api  # noqa: F401


def test_c2_engine_importable():
    from c2.application.simulation.engine import WargameEngine  # noqa: F401
