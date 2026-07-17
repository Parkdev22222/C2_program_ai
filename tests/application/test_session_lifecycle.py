"""Task 29A: WargameSession 골격 + 엔진 생명주기 검증.

- WargameSession이 엔진 생명주기(ensure_engine/register_engine/ensure_ontology/reset/
  start_pause/set_timescale/stop/get_state)를 소유하며, 의존성(engine_factory/
  tool_register_hook/graph_store_factory/agent)은 모두 주입된다.
- 콜백 4종(on_new_opfor_detection/on_blufor_cp_threshold/on_blufor_air_hit/
  on_target_moved)은 ensure_engine()과 reset() 양쪽에서 반드시 (재)등록된다 (CLAUDE.md).
- session.py는 application 계층이므로 c2.domain/c2.application/stdlib 외의 import를
  하지 않는다 (tools/ui/infrastructure import 금지 — 소스 검사로 검증).
"""

import ast
import importlib
import inspect
import tempfile
from pathlib import Path

import pytest


def _make_engine_factory():
    from c2.application.simulation.engine import WargameEngine
    from c2.application.simulation.scenario import setup_bn_vs_bn
    from c2.infrastructure.persistence.sqlite_event_store import WargameDB

    tmp_dir = Path(tempfile.mkdtemp())

    def factory():
        return WargameEngine(setup_bn_vs_bn(), db=WargameDB(tmp_dir / "s.db"))

    return factory


def _make_session(**kwargs):
    from c2.application.simulation.session import WargameSession

    return WargameSession(engine_factory=_make_engine_factory(), **kwargs)


def _assert_all_4_callbacks(engine):
    assert engine.on_new_opfor_detection is not None
    assert engine.on_blufor_cp_threshold is not None
    assert engine.on_blufor_air_hit is not None
    assert engine.on_target_moved is not None


class TestEnsureEngine:
    def test_returns_engine(self):
        session = _make_session()
        engine = session.ensure_engine()
        assert engine is not None

    def test_registers_all_4_callbacks(self):
        session = _make_session()
        engine = session.ensure_engine()
        _assert_all_4_callbacks(engine)

    def test_idempotent_returns_same_engine(self):
        session = _make_session()
        e1 = session.ensure_engine()
        e2 = session.ensure_engine()
        assert e1 is e2

    def test_calls_tool_register_hook_with_engine(self):
        received = []
        session = _make_session(tool_register_hook=lambda eng: received.append(eng))
        engine = session.ensure_engine()
        assert received == [engine]

    def test_no_tool_register_hook_is_noop(self):
        session = _make_session()  # no hook injected
        engine = session.ensure_engine()  # must not raise
        assert engine is not None


class TestReset:
    def test_reset_returns_dict(self):
        session = _make_session()
        session.ensure_engine()
        result = session.reset()
        assert isinstance(result, dict)

    def test_reset_reregisters_all_4_callbacks(self):
        session = _make_session()
        engine = session.ensure_engine()
        # 콜백을 지워서 reset이 실제로 재등록하는지 검증
        engine.on_new_opfor_detection = None
        engine.on_blufor_cp_threshold = None
        engine.on_blufor_air_hit = None
        engine.on_target_moved = None
        session.reset()
        _assert_all_4_callbacks(session.ensure_engine())

    def test_reset_calls_tool_register_hook_again(self):
        received = []
        session = _make_session(tool_register_hook=lambda eng: received.append(eng))
        engine = session.ensure_engine()
        session.reset()
        assert received.count(engine) >= 2

    def test_reset_creates_engine_if_absent(self):
        session = _make_session()
        result = session.reset()
        assert isinstance(result, dict)
        assert session.ensure_engine() is not None


class TestStartPause:
    def test_returns_dict_with_running(self):
        session = _make_session()
        session.ensure_engine()
        result = session.start_pause()
        assert isinstance(result, dict)
        assert "running" in result
        assert "label" in result

    def test_toggles_running_state(self):
        session = _make_session()
        engine = session.ensure_engine()
        assert engine.running is False
        r1 = session.start_pause()
        assert r1["running"] is True
        assert engine.running is True
        r2 = session.start_pause()
        assert r2["running"] is False
        assert engine.running is False
        session.stop()


class TestSetTimescaleAndState:
    def test_set_timescale(self):
        session = _make_session()
        engine = session.ensure_engine()
        session.set_timescale(30.0)
        assert engine.time_scale == 30.0

    def test_get_state_matches_engine(self):
        session = _make_session()
        session.ensure_engine()
        state = session.get_state()
        assert isinstance(state, dict)
        assert "units" in state
        assert "running" in state

    def test_stop(self):
        session = _make_session()
        engine = session.ensure_engine()
        engine.start()
        session.stop()
        assert engine.running is False


class TestEnsureOntology:
    def test_no_graph_store_factory_is_noop(self):
        session = _make_session()  # graph_store_factory=None default
        engine = session.ensure_engine()
        session.ensure_ontology(engine)  # must not raise

    def test_graph_store_factory_invoked(self):
        calls = []

        class FakeGraphStore:
            pass

        session = _make_session(graph_store_factory=lambda: (calls.append(1), FakeGraphStore())[1])
        engine = session.ensure_engine()
        assert calls  # invoked during ensure_engine -> ensure_ontology


class TestArchitecturalPurity:
    def test_no_tools_ui_infrastructure_import_in_source(self):
        """실제 import 문(AST)만 검사 — docstring 속 서술 문장은 오탐 대상 아님."""
        mod = importlib.import_module("c2.application.simulation.session")
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        forbidden_prefixes = ("tools", "ui", "gradio", "c2.infrastructure", "c2.presentation")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes), (
                        f"forbidden import: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                mod_name = node.module or ""
                assert not mod_name.startswith(forbidden_prefixes), (
                    f"forbidden import from: {mod_name}"
                )

    def test_session_class_exists(self):
        from c2.application.simulation.session import WargameSession

        assert WargameSession is not None
