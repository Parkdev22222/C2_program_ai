"""애플리케이션 포트 (Protocol 인터페이스) — 인프라를 역전된 의존성으로 참조한다.

각 포트는 stdlib/typing과 `c2.domain.*`만 임포트한다(인프라 임포트 금지).
"""

from c2.application.ports.conversation_store import ConversationStore
from c2.application.ports.event_store import EventStore
from c2.application.ports.llm import LLMClient
from c2.application.ports.ontology_store import OntologyStore

__all__ = [
    "LLMClient",
    "OntologyStore",
    "EventStore",
    "ConversationStore",
]
