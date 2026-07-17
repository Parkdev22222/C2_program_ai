"""Task 25/33: 에이전트 오케스트레이션 (c2.presentation.agent).

- 3개 이동 모듈이 c2.presentation.agent.<name> 에서 import 가능
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
