"""[shim] 워게임 엔진 구현은 c2.application.simulation.engine 로 이동됨 (Task 20).

애플리케이션 엔진은 EventStore 포트에만 의존하며 인프라(WargameDB)를 import 하지
않는다. 이 레거시 shim(wargame 패키지는 인프라 import 허용)이 기본 EventStore로
WargameDB를 주입해 레거시 `WargameEngine(units)` (db 미주입) 호출을 지원한다.
"""

from c2.application.simulation.engine import (  # noqa: F401  [shim]
    WargameEngine,
    set_default_event_store_factory,
    # 모듈 레벨 상수/헬퍼도 하위 호환 재노출
    DESTROYED_THRESHOLD,
    SUPPRESSED_THRESHOLD,
    DEGRADED_THRESHOLD,
    BASE_ATTRITION_RATE,
    _fmt_time,
)
from c2.infrastructure.persistence.sqlite_event_store import WargameDB  # noqa: F401  [shim]

# 기본 EventStore 팩토리 주입: 레거시 WargameEngine(units) (db 없이) → WargameDB()
set_default_event_store_factory(lambda: WargameDB())
