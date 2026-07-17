"""전술채팅 멀티턴 대화 저장소 포트.

`agent/conversation_store.py`가 이미 동일한(구조적으로 같은) `ConversationStore`
Protocol을 정의하고 있다(`InMemoryConversationStore`, `PostgresConversationStore`가
그 위치에서 구현). 이 모듈은 그 계약을 애플리케이션 계층의 정본(canonical) 위치로
재정의한다 — Task 15(저장소 이관)까지 `agent/conversation_store.py`는 수정하지 않는다.
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable


@runtime_checkable
class ConversationStore(Protocol):
    """세션별 멀티턴 대화 영속화 포트 (`InMemoryConversationStore`,
    `PostgresConversationStore`가 구현)."""

    def append_turn(self, session_id: str, messages: List[dict]) -> None:
        ...

    def recent_turns(self, session_id: str, n_turns: int) -> List[List[dict]]:
        ...

    def clear(self, session_id: str) -> None:
        ...
