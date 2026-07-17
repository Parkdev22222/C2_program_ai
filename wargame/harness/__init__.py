"""[shim] 워게임 하네스 엔지니어링 패키지.

구현은 다음 위치로 이동됨 (Task 26):
  - 오케스트레이션(controller/episode_runner/metrics/rule_extractor/
    rule_manager/tactical_memory) → c2.application.harness
  - SQLite 영속 저장(HarnessDB) → c2.infrastructure.persistence.harness_db

애플리케이션 하네스는 HarnessDB(인프라)를 직접 import 하지 않고 DI 팩토리
(`c2.application.harness.controller.set_default_harness_db_factory`)에
의존한다. 이 레거시 shim(wargame 패키지는 인프라 import 허용)이 기본
HarnessDB를 팩토리로 주입해 레거시 `HarnessController(engine_factory)`
(db 미주입) 호출을 지원한다.

주요 클래스:
    EpisodeMetrics:   단일 에피소드 결과 데이터
    HarnessDB:        에피소드 및 규칙 영속 저장 (SQLite)
    EpisodeRunner:    단일 에피소드 실행기
    RuleExtractor:    에피소드 결과에서 전술 규칙 추출
    RuleManager:      규칙 생명주기 관리 및 파일 동기화
    HarnessController: 전체 학습 루프 조율
"""

from c2.application.harness.metrics import EpisodeMetrics  # noqa: F401  [shim]
from c2.infrastructure.persistence.harness_db import HarnessDB  # noqa: F401  [shim]
from c2.application.harness.episode_runner import EpisodeRunner  # noqa: F401  [shim]
from c2.application.harness.rule_extractor import RuleExtractor  # noqa: F401  [shim]
from c2.application.harness.rule_manager import RuleManager  # noqa: F401  [shim]
from c2.application.harness.controller import (  # noqa: F401  [shim]
    HarnessController,
    set_default_harness_db_factory,
)
from c2.application.harness.tactical_memory import (  # noqa: F401  [shim]
    TacticalMemory,
    SpatialRuleExtractor,
    get_tactical_memory,
)

# 기본 HarnessDB 팩토리 주입: 레거시 HarnessController(engine_factory) (db 없이) → HarnessDB()
set_default_harness_db_factory(lambda: HarnessDB())

__all__ = [
    "EpisodeMetrics",
    "HarnessDB",
    "EpisodeRunner",
    "RuleExtractor",
    "RuleManager",
    "HarnessController",
    "TacticalMemory",
    "SpatialRuleExtractor",
    "get_tactical_memory",
]
