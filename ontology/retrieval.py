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
        newest_first: bool = False,
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
    per_seed_limit: int | None = None,
    hops: int = 1,
) -> tuple[tuple[KnowledgeNode, ...], tuple[KnowledgeEdge, ...], tuple[Evidence, ...]]:
    """seed entity_ids 로 Neo4j(또는 폴백)에서 KG 이웃·엣지·근거를 검색한다.

    호출 순서와 soft-fallback 은 원본 ``RetrieveContextNode.invoke`` 와 동일하다.

    per_seed_limit 을 주면 seed(부대)별로 이웃을 per_seed_limit 개까지만(최신 우선)
    수집한다. 턴이 누적돼도 부대당 정보량이 고정되어 컨텍스트가 무한정 늘지 않는다.

    hops 로 탐색 깊이를 지정한다(기본 1). hops=2 이면 1-hop 으로 찾은 노드의 entity_id 를
    다음 프론티어로 삼아 한 번 더 이웃을 확장한다(예: 아군 → 탐지한 적 → 그 적의 관측·관계).
    """
    since, until = _as_of_window(as_of)

    if per_seed_limit:
        # 부대(seed)별로 최근 per_seed_limit 개 이웃을 hops 깊이까지 BFS 로 수집 후 합집합
        collected: dict[str, KnowledgeNode] = {}
        visited_ids: set[str] = set()
        frontier: list[str] = list(dict.fromkeys(entity_ids))
        for _hop in range(max(1, hops)):
            next_ids: list[str] = []
            for eid in frontier:
                if not eid or eid in visited_ids:
                    continue
                visited_ids.add(eid)
                for node in graph_store.neighborhood(
                    (eid,),
                    limit=per_seed_limit,
                    since=since,
                    until=until,
                    scenario_id=scenario_id,
                    newest_first=True,
                ):
                    if node.kg_node_id not in collected:
                        collected[node.kg_node_id] = node
                        # 다음 hop 프론티어: 새로 찾은 노드의 entity_id(아직 미방문)
                        if node.entity_id and node.entity_id not in visited_ids:
                            next_ids.append(node.entity_id)
            frontier = list(dict.fromkeys(next_ids))
            if not frontier:
                break
        kg_nodes: tuple[KnowledgeNode, ...] = tuple(collected.values())
    else:
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
