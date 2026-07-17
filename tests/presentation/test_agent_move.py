"""Task 25: 에이전트 오케스트레이션 → c2.presentation.agent + shim 검증.

- 3개 이동 모듈이 c2.presentation.agent.<name> 에서 import 가능
- shim identity: agent.<name>.<symbol> is c2.presentation.agent.<name>.<symbol>
  (smolagents/langgraph/langchain 부재 환경에서도 conftest 스텁 덕에 import되는 심볼만 검증)
- 내부 import가 c2 canonical 경로로 갱신되었는지 소스 텍스트로 검증
"""
import importlib
import inspect

MOVED_MODULES = [
    "battlefield_agent",
    "langgraph_agent",
    "langgraph_tools",
]


def test_all_3_modules_importable_from_c2_presentation_agent():
    for name in MOVED_MODULES:
        mod = importlib.import_module(f"c2.presentation.agent.{name}")
        assert mod is not None


def test_shim_identity_for_battlefield_agent_symbols():
    import agent.battlefield_agent as old_ba
    import c2.presentation.agent.battlefield_agent as new_ba

    assert old_ba.BattlefieldAgent is new_ba.BattlefieldAgent
    assert old_ba.build_battlefield_tools is new_ba.build_battlefield_tools
    assert old_ba.append_learned_rule is new_ba.append_learned_rule
    assert old_ba.get_instruction_section is new_ba.get_instruction_section
    assert old_ba.is_strategy_query is new_ba.is_strategy_query
    assert old_ba.classify_intent is new_ba.classify_intent


def test_shim_identity_for_langgraph_agent_symbols():
    import agent.langgraph_agent as old_lga
    import c2.presentation.agent.langgraph_agent as new_lga

    assert old_lga.LangGraphBattlefieldAgent is new_lga.LangGraphBattlefieldAgent


def test_shim_identity_for_langgraph_tools_symbols():
    import agent.langgraph_tools as old_lgt
    import c2.presentation.agent.langgraph_tools as new_lgt

    assert old_lgt.build_langchain_tools is new_lgt.build_langchain_tools
    assert old_lgt.to_langchain_tool is new_lgt.to_langchain_tool


def test_agent_dunder_init_still_exports_battlefield_agent_and_model_loader():
    # agent/__init__.py is NOT moved (still imports .battlefield_agent + .model_loader
    # relatively); confirm it still resolves through the shim after the move.
    import agent

    assert agent.BattlefieldAgent is not None
    assert callable(agent.load_exaone_model)
    assert callable(agent.load_model_from_config_file)


def test_internal_imports_updated_to_c2_canonical_paths():
    import c2.presentation.agent.battlefield_agent as ba

    src = inspect.getsource(ba)
    assert "from tools." not in src
    assert "from agent.model_loader" not in src
    assert "c2.presentation.tools" in src
    assert "c2.infrastructure.llm.model_loader" in src
    assert "c2.application.planning.mission_session" in src


def test_langgraph_agent_internal_imports_updated_to_c2_canonical_paths():
    import c2.presentation.agent.langgraph_agent as lga

    src = inspect.getsource(lga)
    assert "from agent.battlefield_agent" not in src
    assert "from agent.langgraph_llm" not in src
    assert "from agent.langgraph_tools" not in src
    assert "from agent.conversation_store" not in src
    assert "from tools." not in src
    assert "c2.presentation.agent.battlefield_agent" in src
    assert "c2.infrastructure.llm.langgraph_llm" in src
    assert "c2.presentation.agent.langgraph_tools" in src
    assert "c2.infrastructure.persistence.conversation_store" in src
    assert "c2.presentation.tools" in src


def test_langgraph_tools_internal_imports_updated_to_c2_canonical_paths():
    import c2.presentation.agent.langgraph_tools as lgt

    src = inspect.getsource(lgt)
    assert "from agent.battlefield_agent" not in src
    assert "c2.presentation.agent.battlefield_agent" in src


def test_old_agent_modules_are_thin_shims():
    for name in MOVED_MODULES:
        old_mod = importlib.import_module(f"agent.{name}")
        src = inspect.getsource(old_mod)
        # shim files re-export from the new c2 location rather than reimplementing logic
        assert f"c2.presentation.agent.{name}" in src
