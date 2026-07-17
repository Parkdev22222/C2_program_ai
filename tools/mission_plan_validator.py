"""
임무계획 검증기 - 하위호환 shim

구조:
- guard_write_tool(): apply 계열 실행 전 confirmation gate
- pending_plan 세션 상태 관리 (save_pending_plan / get_pending_plan / approve_plan)
- classify_intent(): 사용자 쿼리 의도 분류
- update_valid_company_ids(): 시나리오별 company_id allow-list 동적 갱신

세션/가드/의도분류 로직은 c2.application.planning.mission_session 으로,
Pydantic typed schema(Waypoint/MissionPlanItem/AirSupportItem/MissionPlanRequest)와
MAP_MAX/validate_mission_plan()은 c2.domain.planning.mission_plan 으로 이동했다.
이 모듈은 두 계층의 공개 심볼을 재수출하는 하위호환 shim이다.
"""
from c2.domain.planning import mission_plan as _mission_plan_domain
from c2.domain.planning.mission_plan import (  # noqa: F401  [shim]
    MAP_MAX,
    VALID_MISSION_TYPES,
    VALID_SUPPORT_TYPES,
    validate_mission_plan,
)
try:
    from c2.domain.planning.mission_plan import (  # noqa: F401  [shim]
        Waypoint,
        MissionPlanItem,
        AirSupportItem,
        MissionPlanRequest,
    )
except ImportError:
    pass

from c2.application.planning.mission_session import (  # noqa: F401  [shim]
    WRITE_TOOLS,
    update_valid_company_ids,
    save_pending_plan,
    get_pending_plan,
    approve_plan,
    clear_pending_plan,
    get_session_state,
    guard_write_tool,
    classify_intent,
)


# 하위호환: 과거 `tools.mission_plan_validator.VALID_COMPANY_IDS`를 직접
# 참조하던 코드를 위한 property-like 접근. 모듈 속성으로는 갱신 시점의
# 스냅샷이 아니라 항상 domain 모듈의 최신 값을 가리키도록 __getattr__ 사용.
def __getattr__(name):
    if name == "VALID_COMPANY_IDS":
        return _mission_plan_domain.VALID_COMPANY_IDS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
