"""[shim] 워게임 임무계획 실행 도구는 c2.presentation.tools.wargame_mission_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.wargame_mission_tool import (  # noqa: F401  [shim]
    register_wargame_engine,
    set_resume_on_apply,
    reset_apply_tracker,
    was_plan_applied_since,
    get_last_applied_plan,
    apply_wargame_mission_plan,
    apply_wargame_air_support,
    get_wargame_engine_status,
)
