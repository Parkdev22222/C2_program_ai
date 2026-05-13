"""
워게임 하네스 엔지니어링 패키지.

에피소드 실행, 메트릭 수집, 규칙 추출/관리, DB 저장을 통해
워게임 에이전트의 전술 규칙을 자동으로 학습합니다.

주요 클래스:
    EpisodeMetrics:   단일 에피소드 결과 데이터
    HarnessDB:        에피소드 및 규칙 영속 저장 (SQLite)
    EpisodeRunner:    단일 에피소드 실행기
    RuleExtractor:    에피소드 결과에서 전술 규칙 추출
    RuleManager:      규칙 생명주기 관리 및 파일 동기화
    HarnessController: 전체 학습 루프 조율
"""

from .metrics import EpisodeMetrics
from .harness_db import HarnessDB
from .episode_runner import EpisodeRunner
from .rule_extractor import RuleExtractor
from .rule_manager import RuleManager
from .controller import HarnessController
from .tactical_memory import TacticalMemory, SpatialRuleExtractor, get_tactical_memory

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
