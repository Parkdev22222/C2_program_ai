"""Task 29D: 하니스(학습/평가) 세션 ops → WargameSession 데이터 반환 검증.

- `WargameSession.init_harness_controller/harness_start_training/harness_status/
  harness_stop_training/harness_rules` 는 모두 **dict**(또는 컨트롤러 객체)를
  반환한다 (Gradio 튜플/마크다운 문자열 아님).
- 오케스트레이션은 이미 application인 `c2.application.harness.controller.
  HarnessController`를 세션이 직접 구성/사용한다 (application → application, 허용).
- HarnessDB(인프라)는 DI 팩토리(`set_default_harness_db_factory`)로 주입 —
  테스트는 임시 경로 HarnessDB를 팩토리로 넣어 빠르게 검증한다.
- session.py는 tools/ui/agent/wargame/infrastructure/presentation을 import하지
  않는다 (소스 검사).
"""

import ast
import tempfile
from pathlib import Path

import pytest


# ── 헬퍼 ────────────────────────────────────────────────────────

def _make_engine_factory():
    from c2.application.simulation.engine import WargameEngine
    from c2.application.simulation.scenario import setup_cheorwon_bn
    from c2.infrastructure.persistence.sqlite_event_store import WargameDB

    tmp_dir = Path(tempfile.mkdtemp())

    def factory():
        return WargameEngine(setup_cheorwon_bn(), db=WargameDB(tmp_dir / "s.db"))

    return factory


def _make_session():
    from c2.application.simulation.session import WargameSession

    return WargameSession(engine_factory=_make_engine_factory())


@pytest.fixture()
def temp_harness_db(tmp_path, monkeypatch):
    """HarnessController가 사용할 기본 HarnessDB 팩토리를 임시 경로로 교체한다."""
    from c2.application.harness import controller as controller_mod
    from c2.infrastructure.persistence.harness_db import HarnessDB

    db_path = tmp_path / "harness_test.db"
    monkeypatch.setattr(
        controller_mod, "_default_harness_db_factory", lambda: HarnessDB(db_path)
    )
    return db_path


# ── (a) harness_status — 초기 상태 ─────────────────────────────────

class TestHarnessStatus:
    def test_returns_dict_uninitialized(self):
        session = _make_session()
        result = session.harness_status()
        assert isinstance(result, dict)
        assert result.get("initialized") is False

    def test_returns_dict_after_init(self, temp_harness_db):
        session = _make_session()
        ctrl = session.init_harness_controller()
        assert ctrl is not None

        result = session.harness_status()
        assert isinstance(result, dict)
        assert result["initialized"] is True
        assert "progress" in result and "stats" in result
        assert result["progress"]["status"] == "idle"
        assert "total_episodes" in result["stats"]


# ── (b) harness_rules ────────────────────────────────────────────

class TestHarnessRules:
    def test_returns_dict_uninitialized(self):
        session = _make_session()
        result = session.harness_rules()
        assert isinstance(result, dict)
        assert result.get("initialized") is False

    def test_returns_dict_after_init(self, temp_harness_db):
        session = _make_session()
        session.init_harness_controller()

        result = session.harness_rules()
        assert isinstance(result, dict)
        assert result["initialized"] is True
        assert result["recon_rules"] == []
        assert result["attack_rules"] == []
        assert result["learned_rules"] == []
        assert "penalty_zones" in result


# ── (c) harness_start_training ────────────────────────────────────

class TestHarnessStartTraining:
    def test_dispatches_and_returns_dict(self, temp_harness_db):
        session = _make_session()

        result = session.harness_start_training(n_episodes=0, replan_interval=120)

        assert isinstance(result, dict)
        assert result["ok"] is True
        assert result["reason"] == "started"
        assert "status_message" in result
        assert result["chat_entry"] is not None

        # n_episodes=0 이므로 학습 루프가 즉시 종료된다 (빠른 검증).
        ctrl = session.harness_controller
        ctrl.wait_for_completion(timeout=5)
        assert ctrl.get_progress()["status"] == "done"

    def test_already_running_returns_ok_false(self, temp_harness_db):
        session = _make_session()
        ctrl = session.init_harness_controller()
        ctrl._running = True  # 실행 중 상태를 흉내

        result = session.harness_start_training(n_episodes=1, replan_interval=120)
        assert result["ok"] is False
        assert result["reason"] == "already_running"
        assert result["chat_entry"] is None

        ctrl._running = False  # 정리

    def test_init_failure_returns_ok_false(self, monkeypatch):
        session = _make_session()
        monkeypatch.setattr(session, "init_harness_controller", lambda: None)

        result = session.harness_start_training(n_episodes=1, replan_interval=120)
        assert result["ok"] is False
        assert result["reason"] == "init_failed"
        assert result["chat_entry"] is not None


# ── (d) harness_stop_training ─────────────────────────────────────

class TestHarnessStopTraining:
    def test_not_running_returns_dict(self):
        session = _make_session()
        result = session.harness_stop_training()
        assert isinstance(result, dict)
        assert result["stopped"] is False
        assert result["chat_entry"] is None

    def test_running_stops_and_returns_dict(self, temp_harness_db):
        session = _make_session()
        ctrl = session.init_harness_controller()
        ctrl._running = True

        result = session.harness_stop_training()
        assert result["stopped"] is True
        assert result["chat_entry"] is not None
        assert ctrl._running is False


# ── (e) session.py는 tools/ui/agent/wargame/infra를 import하지 않음 ────

def test_session_source_has_no_forbidden_imports():
    import c2.application.simulation as pkg

    src_path = Path(pkg.__file__).parent / "session.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    forbidden = {"tools", "ui", "agent", "wargame", "ontology", "api",
                 "c2.infrastructure", "c2.presentation"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in forbidden or a.name in forbidden:
                    bad.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in forbidden or node.module in forbidden:
                    bad.append(node.module)
    assert not bad, f"session.py 에 금지된 import: {bad}"
