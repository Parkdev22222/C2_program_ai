"""In-memory graph-store implementation for KG neighborhood retrieval.

원본: prototype-ontology-intelligence(claude/ukraine-event-scenarios-wmre56)
      src/infrastructure/graph_store/in_memory.py
      — 검색 semantics(_in_window / neighborhood / edges_for_nodes /
        evidence_for_edges)를 원본과 동일하게 유지한다. 원본은 생성 시 SampleData를
        받지만, 여기서는 워게임 상태가 실시간으로 흘러들어오므로 빈 스토어로 시작해
        merge_node/merge_edge/merge_evidence 로 갱신할 수 있게 했다(Neo4j 폴백).

Task 16: ontology/in_memory_store.py 에서 c2.infrastructure.ontology.in_memory_store 로 이동.
옛 경로(ontology/in_memory_store.py)는 이 모듈을 재노출하는 shim이다.
"""

from __future__ import annotations

from typing import Iterable

from c2.domain.ontology.models import Evidence, KnowledgeEdge, KnowledgeNode


def _in_window(observed_at: str | None, since: str | None, until: str | None) -> bool:
    """Inclusive ISO-time window check. Items without a timestamp are always kept.

    Some structural/OOB KG rows are day-level dates (``YYYY-MM-DD``), while event
    observations are full timestamps (``YYYY-MM-DDT..Z``). Day-level rows are
    compared against the date prefix of the requested window so a whole-day
    ``as_of`` filter keeps them, while full timestamps get full-precision
    comparison. The presence of ``'T'`` is the discriminator — the same rule the
    Neo4j store applies in Cypher, so both backends filter identically.
    """
    if observed_at is None:
        return True
    comparable = observed_at
    lower = since
    upper = until
    if "T" not in observed_at:
        lower = since[:10] if since else None
        upper = until[:10] if until else None
    if lower is not None and comparable < lower:
        return False
    if upper is not None and comparable > upper:
        return False
    return True


def _in_scenario(item_scenario_id: str, scenario_id: str | None) -> bool:
    return scenario_id is None or item_scenario_id == scenario_id


class InMemoryGraphStore:
    def __init__(
        self,
        nodes: Iterable[KnowledgeNode] = (),
        edges: Iterable[KnowledgeEdge] = (),
        evidences: Iterable[Evidence] = (),
    ) -> None:
        self.nodes = {node.kg_node_id: node for node in nodes}
        self.edges = list(edges)
        self.evidences = {evidence.evidence_id: evidence for evidence in evidences}

    # ------------------------------------------------------------------
    # 검색 (원본 semantics 동일)
    # ------------------------------------------------------------------
    def neighborhood(
        self,
        entity_ids: tuple[str, ...],
        limit: int = 10,
        *,
        since: str | None = None,
        until: str | None = None,
        scenario_id: str | None = None,
        newest_first: bool = False,
    ) -> tuple[KnowledgeNode, ...]:
        entity_set = set(entity_ids)
        # writer 스레드의 동시 write 와 경합하지 않도록 컬렉션 스냅샷 위에서 동작
        nodes_snapshot = dict(self.nodes)
        edges_snapshot = list(self.edges)
        seed_nodes = tuple(
            node
            for node in nodes_snapshot.values()
            if node.entity_id in entity_set
            and _in_scenario(node.scenario_id, scenario_id)
        )
        connected_ids = set(node.kg_node_id for node in seed_nodes)
        for edge in edges_snapshot:
            if not _in_scenario(edge.scenario_id, scenario_id):
                continue
            if (
                edge.source_node_id in connected_ids
                or edge.target_node_id in connected_ids
            ):
                connected_ids.add(edge.source_node_id)
                connected_ids.add(edge.target_node_id)
        candidates = [
            nodes_snapshot[node_id]
            for node_id in connected_ids
            if node_id in nodes_snapshot
            and _in_scenario(nodes_snapshot[node_id].scenario_id, scenario_id)
            and _in_window(nodes_snapshot[node_id].observed_at, since, until)
        ]
        if newest_first:
            # 앵커(observed_at None) 먼저, 그다음 관측/이벤트 최신순 (Neo4j newest_first와 일치)
            untimed = sorted(
                (n for n in candidates if not n.observed_at),
                key=lambda n: n.kg_node_id,
            )
            timed = sorted(
                (n for n in candidates if n.observed_at),
                key=lambda n: (n.observed_at, n.kg_node_id),
                reverse=True,
            )
            ordered = list(untimed) + list(timed)
        else:
            ordered = sorted(
                candidates, key=lambda item: (item.observed_at or "", item.kg_node_id)
            )
        return tuple(ordered[:limit])

    def edges_for_nodes(
        self,
        node_ids: tuple[str, ...],
        limit: int = 20,
        *,
        since: str | None = None,
        until: str | None = None,
        scenario_id: str | None = None,
    ) -> tuple[KnowledgeEdge, ...]:
        node_set = set(node_ids)
        return tuple(
            edge
            for edge in sorted(
                list(self.edges), key=lambda item: (item.observed_at or "", item.kg_edge_id)
            )
            if _in_scenario(edge.scenario_id, scenario_id)
            and (edge.source_node_id in node_set or edge.target_node_id in node_set)
            and _in_window(edge.observed_at, since, until)
        )[:limit]

    def evidence_for_edges(
        self, edge_ids: tuple[str, ...], *, scenario_id: str | None = None
    ) -> tuple[Evidence, ...]:
        edge_set = set(edge_ids)
        evidences_snapshot = dict(self.evidences)
        evidence_ids: list[str] = []
        for edge in list(self.edges):
            if edge.kg_edge_id in edge_set and _in_scenario(
                edge.scenario_id, scenario_id
            ):
                evidence_ids.extend(edge.evidence_ids)
        return tuple(
            evidence
            for evidence_id in dict.fromkeys(evidence_ids)
            if (evidence := evidences_snapshot.get(evidence_id)) is not None
            and _in_scenario(evidence.scenario_id, scenario_id)
        )

    # ------------------------------------------------------------------
    # 실시간 적재 — Neo4jGraphStore 와 동일한 인터페이스 (폴백 시 그대로 사용)
    # ------------------------------------------------------------------
    def merge_node(self, n: KnowledgeNode) -> None:
        self.nodes[n.kg_node_id] = n

    def merge_edge(self, e: KnowledgeEdge) -> None:
        for idx, existing in enumerate(self.edges):
            if existing.kg_edge_id == e.kg_edge_id:
                self.edges[idx] = e
                return
        self.edges.append(e)

    def merge_evidence(self, ev: Evidence) -> None:
        self.evidences[ev.evidence_id] = ev

    def ingest(
        self,
        nodes: Iterable[KnowledgeNode],
        edges: Iterable[KnowledgeEdge],
        evidences: Iterable[Evidence],
    ) -> None:
        for n in nodes:
            self.merge_node(n)
        for e in edges:
            self.merge_edge(e)
        for ev in evidences:
            self.merge_evidence(ev)

    def unit_entity_ids(
        self, *, scenario_id: str | None = None, side: str | None = None
    ) -> tuple[str, ...]:
        """Unit 노드의 entity_id 목록(검색 seed 확보용)."""
        ids: list[str] = []
        for node in list(self.nodes.values()):
            if node.node_type != "Unit":
                continue
            if not _in_scenario(node.scenario_id, scenario_id):
                continue
            if side is not None and node.properties.get("side") != side:
                continue
            ids.append(node.entity_id)
        return tuple(dict.fromkeys(ids))

    def recent_event_nodes(
        self, *, scenario_id: str | None = None, limit: int = 15
    ) -> tuple[KnowledgeNode, ...]:
        """최근 전투/포격 이벤트(Event) 노드를 최신순으로 반환.

        관측(Observation) 노드가 매 틱 생성돼 이웃 검색에서 이벤트가 밀려나는 것을 막기 위해,
        이벤트는 별도로 최신 N건을 직접 확보한다.
        """
        evs = [
            n for n in self.nodes.values()
            if n.node_type == "Event" and _in_scenario(n.scenario_id, scenario_id)
        ]
        evs.sort(key=lambda n: (n.observed_at or "", n.kg_node_id), reverse=True)
        return tuple(evs[:limit])

    def reset_demo_data(self) -> None:
        self.nodes.clear()
        self.edges.clear()
        self.evidences.clear()

    def close(self) -> None:  # 인터페이스 호환용
        pass
