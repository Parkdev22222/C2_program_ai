"""온톨로지 그래프 스토어 포트.

`ontology/graph_store.py`의 `Neo4jGraphStore`와 `ontology/in_memory_store.py`의
`InMemoryGraphStore`가 공유하는 공개 인터페이스. 두 구현의 메서드 시그니처는
동일하므로(불일치 없음) 그대로 포트로 승격한다.
"""

from __future__ import annotations

from typing import Iterable, Optional, Protocol, Tuple, runtime_checkable

from c2.domain.ontology.models import Evidence, KnowledgeEdge, KnowledgeNode


@runtime_checkable
class OntologyStore(Protocol):
    """KG 검색/적재 포트 (`Neo4jGraphStore`, `InMemoryGraphStore`가 구현)."""

    def neighborhood(
        self,
        entity_ids: Tuple[str, ...],
        limit: int = 10,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        scenario_id: Optional[str] = None,
        newest_first: bool = False,
    ) -> Tuple[KnowledgeNode, ...]:
        ...

    def edges_for_nodes(
        self,
        node_ids: Tuple[str, ...],
        limit: int = 20,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        scenario_id: Optional[str] = None,
    ) -> Tuple[KnowledgeEdge, ...]:
        ...

    def evidence_for_edges(
        self, edge_ids: Tuple[str, ...], *, scenario_id: Optional[str] = None
    ) -> Tuple[Evidence, ...]:
        ...

    def merge_node(self, n: KnowledgeNode) -> None:
        ...

    def merge_edge(self, e: KnowledgeEdge) -> None:
        ...

    def merge_evidence(self, ev: Evidence) -> None:
        ...

    def ingest(
        self,
        nodes: Iterable[KnowledgeNode],
        edges: Iterable[KnowledgeEdge],
        evidences: Iterable[Evidence],
    ) -> None:
        ...

    def unit_entity_ids(
        self, *, scenario_id: Optional[str] = None, side: Optional[str] = None
    ) -> Tuple[str, ...]:
        ...

    def recent_event_nodes(
        self, *, scenario_id: Optional[str] = None, limit: int = 15
    ) -> Tuple[KnowledgeNode, ...]:
        ...

    def reset_demo_data(self) -> None:
        ...

    def close(self) -> None:
        ...
