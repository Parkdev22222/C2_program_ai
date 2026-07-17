"""하네스 엔지니어링(학습/평가) 영속 저장소 포트.

`c2.infrastructure.persistence.harness_db.HarnessDB`(SQLite CRUD)가 이 포트를
구현한다. `c2.application.harness.controller` / `rule_manager`는 이 Protocol을
타입힌트로만 참조하여, 구체 구현(HarnessDB, 인프라 계층)을 import 하지 않고도
정적 타입 검사와 의존성 역전을 동시에 만족한다 (Task 20의 EventStore 포트와
동일 패턴).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from c2.application.harness.metrics import EpisodeMetrics


@runtime_checkable
class HarnessStore(Protocol):
    """하네스 에피소드/규칙 영속화 포트 (`HarnessDB`가 구현)."""

    # ── 에피소드 CRUD ─────────────────────────────────────────────
    def save_episode(self, metrics: "EpisodeMetrics", active_rule_ids: List[str]) -> None:
        ...

    def get_episode(self, episode_id: str) -> Optional[dict]:
        ...

    def get_all_episodes(self) -> List[dict]:
        ...

    def get_win_rate(self, last_n: int = 20) -> float:
        ...

    # ── 규칙 CRUD ─────────────────────────────────────────────────
    def save_rule(
        self,
        rule_id: str,
        text: str,
        section: str,
        confidence: float,
        source_episode: str,
    ) -> None:
        ...

    def update_rule_effectiveness(self, rule_ids: List[str], winner: str) -> None:
        ...

    def get_active_rules(self, section: Optional[str] = None) -> List[dict]:
        ...

    def deactivate_rule(self, rule_id: str) -> None:
        ...

    def delete_rule(self, rule_id: str) -> None:
        ...

    def get_stats(self) -> dict:
        ...
