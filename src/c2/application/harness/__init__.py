"""
워게임 하네스 엔지니어링 — 애플리케이션 계층.

[Task 26] wargame/harness/ 에서 이동. 에피소드 실행, 메트릭 수집,
규칙 추출/관리, 전술 메모리, 학습 루프 조율을 담당한다.
SQLite 영속 저장(HarnessDB)은 인프라 계층
(`c2.infrastructure.persistence.harness_db`)에 위치하며, 이 패키지는
DI 팩토리(`controller.set_default_harness_db_factory`)를 통해서만
간접적으로 사용한다 (application → infrastructure import 금지).

주요 클래스:
    EpisodeMetrics:   단일 에피소드 결과 데이터
    EpisodeRunner:    단일 에피소드 실행기
    RuleExtractor:    에피소드 결과에서 전술 규칙 추출
    RuleManager:      규칙 생명주기 관리 및 파일 동기화
    HarnessController: 전체 학습 루프 조율
"""

from .metrics import EpisodeMetrics
from .episode_runner import EpisodeRunner
from .rule_extractor import RuleExtractor
from .rule_manager import RuleManager
from .controller import HarnessController, set_default_harness_db_factory
from .tactical_memory import TacticalMemory, SpatialRuleExtractor, get_tactical_memory

__all__ = [
    "EpisodeMetrics",
    "EpisodeRunner",
    "RuleExtractor",
    "RuleManager",
    "HarnessController",
    "set_default_harness_db_factory",
    "TacticalMemory",
    "SpatialRuleExtractor",
    "get_tactical_memory",
]
