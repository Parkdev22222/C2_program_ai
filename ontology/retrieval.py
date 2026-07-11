"""KG 검색 오케스트레이션.

prototype-ontology-intelligence(claude/ukraine-event-scenarios-wmre56)
src/core/retrieval/base.py 의 ``RetrieveContextNode.invoke`` 가 수행하는
Neo4j 검색 순서를 그대로 옮긴 것이다:

    neighborhood(entity_ids)                       # 1-hop 이웃 노드
      → edges_for_nodes(kg_node_ids)               # 그 노드들에 걸린 엣지
        → evidence_for_edges(kg_edge_ids)          # 엣지에 연결된 근거

as_of(날짜/타임스탬프) → since/until 윈도우 변환과, 해당 일자에 KG가 비면
무시간(untimed) 재조회로 degrade 하는 soft-fallback 도 원본과 동일하다.
``_as_of_window`` 와 ``GraphStore`` 프로토콜은 원본에서 그대로 가져왔다.
"""

from __future__ import annotations

from typing import Protocol

from ontology.models import Evidence, KnowledgeEdge, KnowledgeNode


class GraphStore(Protocol):
    def neighborhood(
        self,
        entity_ids: tuple[str, ...],
        limit: int = 10,
        *,
        since: str | None = None,
        until: str | None = None,
        scenario_id: str | None = None,
    ) -> tuple[KnowledgeNode, ...]: ...
    def edges_for_nodes(
        self,
        node_ids: tuple[str, ...],
        limit: int = 20,
        *,
        since: str | None = None,
        until: str | None = None,
        scenario_id: str | None = None,
    ) -> tuple[KnowledgeEdge, ...]: ...
    def evidence_for_edges(
        self, edge_ids: tuple[str, ...], *, scenario_id: str | None = None
    ) -> tuple[Evidence, ...]: ...


def _as_of_window(as_of: str | None) -> tuple[str | None, str | None]:
    """Turn an ``as_of`` (date or ISO timestamp) into an inclusive day window.

    ``2026-06-24`` / ``2026-06-24T10:00:00Z`` -> ``(2026-06-24T00:00:00Z,
    2026-06-24T23:59:59Z)``. Returns ``(None, None)`` when ``as_of`` is unset so
    callers apply no temporal filter.
    """
    if not as_of:
        return None, None
    day = as_of[:10]
    return f"{day}T00:00:00Z", f"{day}T23:59:59Z"


def retrieve_graph_context(
    graph_store: GraphStore,
    entity_ids: tuple[str, ...],
    *,
    scenario_id: str | None = None,
    as_of: str | None = None,
    node_limit: int = 10,
    edge_limit: int = 20,
) -> tuple[tuple[KnowledgeNode, ...], tuple[KnowledgeEdge, ...], tuple[Evidence, ...]]:
    """seed entity_ids 로 Neo4j(또는 폴백)에서 KG 이웃·엣지·근거를 검색한다.

    호출 순서와 soft-fallback 은 원본 ``RetrieveContextNode.invoke`` 와 동일하다.
    """
    since, until = _as_of_window(as_of)

    kg_nodes = graph_store.neighborhood(
        entity_ids,
        limit=node_limit,
        since=since,
        until=until,
        scenario_id=scenario_id,
    )
    if since is not None and entity_ids and not kg_nodes:
        # 요청 일자에 KG 타임라인이 없으면 무시간 재조회로 degrade (원본 동일).
        since = until = None
        kg_nodes = graph_store.neighborhood(
            entity_ids,
            limit=node_limit,
            scenario_id=scenario_id,
        )
    kg_edges = graph_store.edges_for_nodes(
        tuple(node.kg_node_id for node in kg_nodes),
        limit=edge_limit,
        since=since,
        until=until,
        scenario_id=scenario_id,
    )
    evidences = graph_store.evidence_for_edges(
        tuple(edge.kg_edge_id for edge in kg_edges),
        scenario_id=scenario_id,
    )
    return kg_nodes, kg_edges, evidences
