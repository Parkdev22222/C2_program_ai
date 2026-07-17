"""[shim] 화력지원 타격 우선순위 도구는 c2.presentation.tools.wargame_fire_priority_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.wargame_fire_priority_tool import (  # noqa: F401  [shim]
    register_wargame_engine,
    get_fire_priority_schedule,
)
