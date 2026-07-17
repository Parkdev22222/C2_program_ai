"""Task 29B: 자동 재계획 워커 + 탐지 워커 → c2.application.simulation.replan 검증.

- WargameSession이 탐지 큐 + enqueue 4종 + (start/stop)_detection_worker를 소유하며,
  엔진 콜백 4종이 세션 enqueue → 세션 큐 → 세션 워커 → replan.execute_auto_attack_plan
  으로 흐른다 (CLAUDE.md 콜백 4종 유지).
- replan.execute_auto_attack_plan은 주입된 (모의) agent를 호출하고, 산출 계획을
  엔진에 적용한다.
- replan.py/session.py는 application 계층이므로 tools/ui/infrastructure를 import하지
  않는다 (소스 검사).
"""

import ast
import json
import queue
import tempfile
from pathlib import Path

import pytest


# ── 헬퍼 ────────────────────────────────────────────────────────

def _make_engine_factory():
    from c2.application.simulation.engine import WargameEngine
    from c2.application.simulation.scenario import setup_bn_vs_bn
    from c2.infrastructure.persistence.sqlite_event_store import WargameDB

    tmp_dir = Path(tempfile.mkdtemp())

    def factory():
        return WargameEngine(setup_bn_vs_bn(), db=WargameDB(tmp_dir / "s.db"))

    return factory


class _MockInnerAgent:
    """agent.agent.run(query, reset=True) 를 흉내내는 내부 러너."""

    def __init__(self, plan_json: str):
        self._plan_json = plan_json
        self.calls = []

    def run(self, query, reset=True):
        self.calls.append(query)
        return self._plan_json


class _MockAgent:
    """세션에 주입되는 agent 래퍼 (battlefield_agent 유사 인터페이스)."""

    def __init__(self, plan_json: str):
        self.agent = _MockInnerAgent(plan_json)
        self.reset_count = 0

    def reset_memory(self):
        self.reset_count += 1


def _make_session(agent=None):
    from c2.application.simulation.session import WargameSession

    return WargameSession(engine_factory=_make_engine_factory(), agent=agent)


def _canned_plan_json(blu_id: str) -> str:
    return json.dumps(
        {
            "reasoning": "테스트 계획",
            "mission_plans": [
                {
                    "company_id": blu_id,
                    "mission_type": "attack",
                    "waypoints": [[15000, 15000]],
                    "objective": "테스트 공격",
                }
            ],
        },
        ensure_ascii=False,
    )


# ── (a) enqueue 4종 → 세션 큐 ────────────────────────────────────

class TestEnqueuePushesToSessionQueue:
    def test_enqueue_detection(self):
        session = _make_session()
        session.enqueue_detection("Red1", "전차", 20000, 20000)
        evt = session.detection_queue.get_nowait()
        assert evt == ("detection", "Red1", "전차", 20000, 20000)

    def test_enqueue_cp_threshold(self):
        session = _make_session()
        session.enqueue_cp_threshold("Alpha", "보병", 70.0, 65.0)
        evt = session.detection_queue.get_nowait()
        assert evt == ("cp_threshold", "Alpha", "보병", 70.0, 65.0)

    def test_enqueue_air_hit(self):
        session = _make_session()
        session.enqueue_air_hit("Bravo", "보병", "EAGLE-1", 55.0)
        evt = session.detection_queue.get_nowait()
        assert evt == ("air_hit", "Bravo", "보병", "EAGLE-1", 55.0)

    def test_enqueue_target_moved(self):
        session = _make_session()
        session.enqueue_target_moved("Charlie", "보병", "Red2", 1500.0)
        evt = session.detection_queue.get_nowait()
        assert evt == ("target_moved", "Charlie", "보병", "Red2", 1500.0)


# ── 콜백 4종이 세션 enqueue로 등록되어 큐로 흐름 ────────────────

class TestEngineCallbacksFlowToSessionQueue:
    def test_all_4_callbacks_enqueue_to_session_queue(self):
        session = _make_session()
        engine = session.ensure_engine()

        engine.on_new_opfor_detection("Red1", "전차", 21000, 21000)
        engine.on_blufor_cp_threshold("Alpha", "보병", 70.0, 60.0)
        engine.on_blufor_air_hit("Bravo", "보병", "EAGLE-1", 50.0)
        engine.on_target_moved("Charlie", "보병", "Red2", 1200.0)

        kinds = []
        while True:
            try:
                kinds.append(session.detection_queue.get_nowait()[0])
            except queue.Empty:
                break
        assert kinds == ["detection", "cp_threshold", "air_hit", "target_moved"]


# ── (b) detection 이벤트 → 워커가 agent 호출 + 엔진에 계획 적용 ──

class TestExecuteAutoAttackPlanDetection:
    def test_agent_invoked_and_plan_applied(self):
        from c2.application.simulation import replan

        session = _make_session()
        engine = session.ensure_engine()
        blu_id = next(
            u["id"] for u in engine.get_state()["units"] if u["side"] == "BLUFOR"
        )
        agent = _MockAgent(_canned_plan_json(blu_id))
        session.agent = agent

        applied = []
        _orig = engine.apply_mission_plan
        engine.apply_mission_plan = lambda plan: applied.append(plan) or _orig(plan)

        replan.execute_auto_attack_plan(session, "detection", "Red1", "전차", 20000, 20000)

        assert agent.agent.calls, "mock agent.run 가 호출되지 않음"
        assert applied, "엔진 apply_mission_plan 이 호출되지 않음 (plan-apply 경로 미실행)"


# ── (c) 4개 이벤트 브랜치 모두 도달 가능 ────────────────────────

class TestAllFourEventBranchesReachable:
    @pytest.mark.parametrize(
        "event",
        [
            ("detection", "Red1", "전차", 20000, 20000),
            ("cp_threshold", "Alpha", "보병", 70.0, 60.0),
            ("air_hit", "Bravo", "보병", "EAGLE-1", 50.0),
            ("target_moved", "Charlie", "보병", "Red2", 1200.0),
        ],
    )
    def test_branch_runs_without_crash_and_calls_agent(self, event):
        from c2.application.simulation import replan

        session = _make_session()
        engine = session.ensure_engine()
        blu_id = next(
            u["id"] for u in engine.get_state()["units"] if u["side"] == "BLUFOR"
        )
        agent = _MockAgent(_canned_plan_json(blu_id))
        session.agent = agent

        replan.execute_auto_attack_plan(session, *event)

        assert agent.agent.calls, f"{event[0]} 브랜치에서 agent 미호출"


# ── 세션 워커(스레드) 경유로도 처리 ────────────────────────────

class TestDetectionWorkerThread:
    def test_worker_thread_dispatches_detection(self):
        session = _make_session()
        engine = session.ensure_engine()
        blu_id = next(
            u["id"] for u in engine.get_state()["units"] if u["side"] == "BLUFOR"
        )
        agent = _MockAgent(_canned_plan_json(blu_id))
        session.agent = agent

        session.start_detection_worker()
        try:
            session.enqueue_detection("Red1", "전차", 20000, 20000)
            import time

            deadline = time.time() + 10.0
            while not agent.agent.calls and time.time() < deadline:
                time.sleep(0.1)
        finally:
            session.stop_detection_worker()

        assert agent.agent.calls, "워커 스레드가 detection 이벤트를 디스패치하지 않음"


# ── (d) replan.py / session.py 는 tools/ui/infra 를 import 하지 않음 ──

class TestNoOutwardImports:
    @pytest.mark.parametrize("mod", ["replan", "session"])
    def test_source_has_no_forbidden_imports(self, mod):
        import c2.application.simulation as pkg

        src_path = Path(pkg.__file__).parent / f"{mod}.py"
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
        assert not bad, f"{mod}.py 에 금지된 import: {bad}"
