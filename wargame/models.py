"""워게임 데이터 모델 및 SQLite 영속성 레이어 — shim.

Unit/AirSupport/AIR_SUPPORT_PRESETS: c2.domain.wargame.unit 로 이동.
WargameDB/DB_PATH: c2.infrastructure.persistence.sqlite_event_store 로 이동.

이 모듈은 하위 호환을 위한 순수 재노출(shim)이며 네이티브 클래스 정의는 없다.
"""

from c2.domain.wargame.unit import (  # noqa: F401  [shim]
    Unit,
    AirSupport,
    AIR_SUPPORT_PRESETS,
)
from c2.infrastructure.persistence.sqlite_event_store import (  # noqa: F401  [shim]
    WargameDB,
    DB_PATH,
)
