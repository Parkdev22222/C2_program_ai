"""[shim] 에이전트 오케스트레이션(BattlefieldAgent)은 c2.presentation.agent.battlefield_agent 로 이동됨 (Task 25)."""
from c2.presentation.agent.battlefield_agent import (  # noqa: F401  [shim]
    CONFIG_DIR,
    INSTRUCTIONS_FILE,
    BattlefieldAgent,
    append_learned_rule,
    build_battlefield_tools,
    classify_intent,
    get_instruction_section,
    is_strategy_query,
)

__all__ = [
    "CONFIG_DIR",
    "INSTRUCTIONS_FILE",
    "BattlefieldAgent",
    "append_learned_rule",
    "build_battlefield_tools",
    "classify_intent",
    "get_instruction_section",
    "is_strategy_query",
]
