"""전술채팅 멀티턴 대화 저장소 — shim.

ConversationStore(Protocol): c2.application.ports.conversation_store 의 정본 재노출.
InMemoryConversationStore / PostgresConversationStore / build_conversation_store:
    c2.infrastructure.persistence.conversation_store 로 이동.

이 모듈은 하위 호환을 위한 순수 재노출(shim)이며 네이티브 클래스 정의는 없다.
"""

from c2.application.ports.conversation_store import ConversationStore  # noqa: F401  [shim]
from c2.infrastructure.persistence.conversation_store import (  # noqa: F401  [shim]
    InMemoryConversationStore,
    PostgresConversationStore,
    build_conversation_store,
)
