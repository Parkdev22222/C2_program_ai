"""Task 29C: 세션 ops(시나리오적용·정찰·공격·채팅·평가) → WargameSession 데이터 반환 검증.

- `WargameSession.apply_custom_scenario/request_recon_plan/request_attack_plan/
  chat_send/evaluate_and_learn` 는 모두 **dict**를 반환한다 (Gradio 튜플/figure 아님).
- 오케스트레이션(에이전트 호출, 플랜 적용-with-repair, replan_hooks 연동)은
  `c2.application.simulation.replan`으로 이식되며, session은 이를 위임한다.
- session.py/replan.py는 application 계층이므로 tools/ui/agent/wargame/ontology/
  infrastructure/presentation을 import하지 않는다 (소스 검사).
"""

import ast
import json
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
    """agent.agent.run(query, reset=True) — 자동/공격 임무계획 경로가 사용."""

    def __init__(self, plan_json: str):
        self._plan_json = plan_json
        self.calls = []

    def run(self, query, reset=True):
        self.calls.append(query)
        return self._plan_json


class _MockAgent:
    """세션에 주입되는 agent 래퍼 (BattlefieldAgent 유사 인터페이스).

    `.run()` (정찰/채팅/평가 경로) 와 `.agent.run()` (공격/자동재계획 경로) 를
    모두 제공한다.
    """

    def __init__(self, plan_json: str = "", run_text: str = ""):
        self.agent = _MockInnerAgent(plan_json)
        self._run_text = run_text or plan_json
        self.run_calls = []
        self.reset_count = 0

    def run(self, query, reset=False):
        self.run_calls.append(query)
        return self._run_text

    def reset_memory(self):
        self.reset_count += 1


class _FakeReplanHooks:
    """presentation 툴 연동을 흉내내는 테스트용 훅. 호출 여부를 기록한다."""

    def __init__(self, assess=None, recon=None):
        self._assess = assess or {"recommendation": "정찰 필요", "opfor_summary": {"detected": 0, "approximate": 1, "lost": 0}}
        self._recon = recon or {}
        self.calls = []
        self.learned_rules = []
        self._resume = False

    def reset_apply_tracker(self):
        self.calls.append("reset_apply_tracker")

    def was_plan_applied_since(self, ts):
        return False

    def get_last_applied_plan(self):
        return {}

    def set_resume_on_apply(self, value):
        self._resume = value
        self.calls.append(("set_resume_on_apply", value))

    def get_instruction_section(self, name):
        return ""

    def assess_recon_need(self):
        self.calls.append("assess_recon_need")
        return self._assess

    def recommend_recon_routes(self):
        self.calls.append("recommend_recon_routes")
        return self._recon

    def append_learned_rule(self, rule):
        self.learned_rules.append(rule)


def _make_session(agent=None, hooks=None):
    from c2.application.simulation.session import WargameSession

    return WargameSession(
        engine_factory=_make_engine_factory(), agent=agent, replan_hooks=hooks
    )


# ── (a) request_recon_plan ───────────────────────────────────────

class TestRequestReconPlan:
    def test_returns_dict_and_invokes_agent(self):
        recon_plan = {
            "mission_plans": [
                {
                    "company_id": "Delta",
                    "mission_type": "recon",
                    "waypoints": [[5000, 5000]],
                    "objective": "측방 관측",
                }
            ]
        }
        recon_result = {
            "status": "success",
            "apply_json": json.dumps(recon_plan, ensure_ascii=False),
            "mission_plans": recon_plan["mission_plans"],
        }
        agent_response = (
            "정찰 임무계획 생성\n```json\n" + json.dumps(recon_plan, ensure_ascii=False) + "\n```"
        )
        agent = _MockAgent(run_text=agent_response)
        hooks = _FakeReplanHooks(recon=recon_result)
        session = _make_session(agent=agent, hooks=hooks)
        session.ensure_engine()

        result = session.request_recon_plan()

        assert isinstance(result, dict)
        assert "history" in result and "plan_text" in result
        assert result["history"], "history가 비어있음"
        assert agent.run_calls, "정찰 임무계획이 agent.run()을 호출하지 않음"
        assert "recommend_recon_routes" in hooks.calls
        # figure/plotly 객체가 섞여있지 않아야 함 (순수 데이터)
        assert "fig" not in result and "damage_fig" not in result

    def test_no_recon_units_returns_dict(self):
        hooks = _FakeReplanHooks(recon={"status": "no_recon_units"})
        session = _make_session(agent=None, hooks=hooks)
        session.ensure_engine()

        result = session.request_recon_plan()
        assert isinstance(result, dict)
        assert "history" in result


# ── (b) request_attack_plan ───────────────────────────────────────

class TestRequestAttackPlan:
    def test_returns_dict_and_applies_plan_to_engine(self):
        attack_plan = {
            "reasoning": "테스트 공격",
            "mission_plans": [
                {
                    "company_id": "Alpha",
                    "mission_type": "attack",
                    "waypoints": [[20000, 20000]],
                    "objective": "적 공격",
                }
            ],
        }
        agent = _MockAgent(plan_json=json.dumps(attack_plan, ensure_ascii=False))
        hooks = _FakeReplanHooks()
        session = _make_session(agent=agent, hooks=hooks)
        eng = session.ensure_engine()

        result = session.request_attack_plan()

        assert isinstance(result, dict)
        assert "history" in result and "plan_text" in result
        assert agent.agent.calls, "공격 임무계획이 agent.agent.run()을 호출하지 않음"

        alpha = next(u for u in eng.units if u.id == "Alpha")
        assert alpha.waypoints, "Alpha 부대에 waypoints가 적용되지 않음"
        assert list(alpha.waypoints[-1][:2]) == [20000, 20000]


# ── (c) chat_send ──────────────────────────────────────────────────

class TestChatSend:
    def test_returns_dict(self):
        agent = _MockAgent(run_text="공격 계획을 검토했습니다.")
        session = _make_session(agent=agent, hooks=_FakeReplanHooks())
        session.ensure_engine()

        result = session.chat_send("적 기갑 공격")

        assert isinstance(result, dict)
        assert "history" in result
        assert result["history"][-1][0] == "적 기갑 공격"
        assert agent.run_calls

    def test_empty_message_returns_dict_unchanged(self):
        session = _make_session(agent=None, hooks=_FakeReplanHooks())
        result = session.chat_send("   ", history=[("a", "b")])
        assert isinstance(result, dict)
        assert result["history"] == [("a", "b")]


# ── (d) apply_custom_scenario ─────────────────────────────────────

class TestApplyCustomScenario:
    def test_returns_dict_and_resets_engine(self):
        session = _make_session(agent=None, hooks=_FakeReplanHooks())
        session.ensure_engine()

        cfg = {
            "blufor": [{"id": "Alpha", "unit_type": "기계화보병", "x": 3000, "y": 3000}],
            "opfor": [{"id": "Red1", "unit_type": "전차", "x": 20000, "y": 20000}],
        }
        result = session.apply_custom_scenario(cfg)

        assert isinstance(result, dict)
        assert result.get("ok") is True
        assert result["blufor"] == 1
        assert result["opfor"] == 1
        ids = {u.id for u in session.engine.units}
        assert ids == {"Alpha", "Red1"}

    def test_missing_blufor_returns_error_dict(self):
        session = _make_session(agent=None, hooks=_FakeReplanHooks())
        result = session.apply_custom_scenario({"blufor": [], "opfor": [{"id": "Red1"}]})
        assert result.get("ok") is False
        assert "error" in result


# ── (e) evaluate_and_learn ─────────────────────────────────────────

class TestEvaluateAndLearn:
    def test_returns_dict(self):
        agent = _MockAgent(run_text="- 고지대 선점 시 화력우위 확보 효과적\n- 근거리 교전 시 기계화보병 지원 필요")
        hooks = _FakeReplanHooks()
        session = _make_session(agent=agent, hooks=hooks)
        session.ensure_engine()

        result = session.evaluate_and_learn()

        assert isinstance(result, dict)
        assert "history" in result
        assert result["history"], "history가 비어있음"


# ── (f) session.py / replan.py 는 tools/ui/infra 를 import 하지 않음 ──

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
