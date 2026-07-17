"""[shim] 워게임 전술 자문 도구는 c2.presentation.tools.wargame_strategy_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.wargame_strategy_tool import (  # noqa: F401  [shim]
    register_wargame_engine,
    get_wargame_tactical_recommendation,
)
