"""[shim] 워게임 상황 조회 도구는 c2.presentation.tools.wargame_query_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.wargame_query_tool import (  # noqa: F401  [shim]
    register_wargame_engine,
    get_wargame_situation,
    get_intelligence_report,
    get_wargame_unit_detail,
    get_wargame_battle_log,
)
