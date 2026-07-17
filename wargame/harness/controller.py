"""[shim] 컨트롤러 구현은 c2.application.harness.controller 로 이동됨 (Task 26).

애플리케이션 컨트롤러는 HarnessDB(인프라)를 직접 import 하지 않고 DI 팩토리에
의존한다. 이 레거시 shim(wargame 패키지는 인프라 import 허용)이 기본
HarnessDB를 팩토리로 주입해 레거시 `HarnessController(engine_factory)`
(db 미주입) 호출을 지원한다. 실제 팩토리 wiring은 패키지 최초 로드 시
`wargame/harness/__init__.py`에서 수행된다 (여기서도 wiring 되어 있으면
멱등하게 재설정됨).
"""

from c2.application.harness.controller import (  # noqa: F401  [shim]
    HarnessController,
    set_default_harness_db_factory,
)
from c2.infrastructure.persistence.harness_db import HarnessDB  # noqa: F401  [shim]

# 기본 HarnessDB 팩토리 주입: 레거시 HarnessController(engine_factory) (db 없이) → HarnessDB()
set_default_harness_db_factory(lambda: HarnessDB())
