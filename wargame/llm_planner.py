"""[shim] LLM 임무계획 생성기는 c2.application.agent.mission_planner 로 이동됨 (Task 22).

이 모듈은 하위 호환을 위한 재노출(shim)이다. 애플리케이션 모듈은 tools/ui/wargame
(legacy) 를 import 하지 않도록 정찰·공격·화력 자문(advisor)을 DI 로 받는다.
이 레거시 shim(wargame 패키지는 tools import 허용)이 실제 툴 3종을 주입해
기존 동작(build_mission_query 가 실제 툴 결과를 프롬프트에 주입)을 보존한다.

import 실패(의존성 누락) 시 advisor 는 None 으로 남고, build_mission_query 는 원본
try/except 와 동일한 graceful fallback dict 를 사용한다.
"""

from c2.application.agent.mission_planner import (  # noqa: F401  [shim]
    build_mission_query,
    MissionPlanner,
    set_planning_advisors,
    _sample_elevation_map,
    _blufor_roster_block,
    _opfor_targets_block,
    _build_mission_query_funccall,
    _FEW_SHOT_EXAMPLES,
    _planning_advisors,
)

# ── 자문(advisor) 주입: wargame(legacy)→tools 는 허용 (순환은 application 밖에서만) ──
try:
    from tools.wargame_recon_tool import recommend_recon_routes
    from tools.wargame_attack_advisor_tool import get_optimal_attack_positions
    from tools.wargame_fire_priority_tool import get_fire_priority_schedule

    set_planning_advisors(
        recon=recommend_recon_routes,
        attack=get_optimal_attack_positions,
        fire=get_fire_priority_schedule,
    )
except Exception:  # 의존성 누락 등 → advisor 는 None 유지 (graceful fallback)
    pass
