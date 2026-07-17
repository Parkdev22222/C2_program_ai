"""[shim] 상황 분석 세션 메모리는 c2.presentation.tools.strategy_advisor_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.strategy_advisor_tool import (  # noqa: F401  [shim]
    update_situation_memory,
    get_situation_memory,
    clear_situation_memory,
)
