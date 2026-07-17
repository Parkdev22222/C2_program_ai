"""[shim] LangGraph 에이전트 오케스트레이션은 c2.presentation.agent.langgraph_agent 로 이동됨 (Task 25)."""
from c2.presentation.agent.langgraph_agent import (  # noqa: F401  [shim]
    LangGraphBattlefieldAgent,
)

__all__ = ["LangGraphBattlefieldAgent"]
