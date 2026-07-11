"""Neo4j-backed implementation of the ``GraphStore`` port.

KG traversal runs as Cypher against Neo4j. The ``neo4j`` driver is imported
lazily so the package keeps importing where the driver/server is absent.

Wiring: set ``OI_NEO4J_URI`` / ``OI_NEO4J_USER`` / ``OI_NEO4J_PASSWORD`` and
select the backend at the composition root.

Modeled as ``(:KgNode {kg_node_id, scenario_id, entity_id, label, node_type,
security_level})`` connected by ``[:REL {kg_edge_id, relation, evidence_ids,
security_level}]``. Edge-linked evidence is stored as ``(:Evidence {...})`` nodes.

원본: prototype-ontology-intelligence(claude/ukraine-event-scenarios-wmre56)
      src/infrastructure/graph_store/neo4j.py
      — Cypher 검색 쿼리(neighborhood / edges_for_nodes / evidence_for_edges)와
        MERGE 적재 쿼리를 원문 그대로 유지한다. import 경로만 ontology.models로 조정하고,
        실시간 적재를 위해 단건 MERGE 헬퍼(merge_node/merge_edge/merge_evidence)를 추가했다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

from ontology.models import Evidence, KnowledgeEdge, KnowledgeNode


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "Neo4jConfig":
        uri = os.environ.get("OI_NEO4J_URI")
        if not uri:
            raise RuntimeError("OI_NEO4J_URI is not set; cannot use the Neo4j backend.")
        return cls(
            uri=uri,
            user=os.environ.get("OI_NEO4J_USER", "neo4j"),
            password=os.environ.get("OI_NEO4J_PASSWORD", ""),
        )


def _node(row: dict[str, Any]) -> KnowledgeNode:
    return KnowledgeNode(
        row["kg_node_id"],
        row["scenario_id"],
        row["entity_id"],
        row["label"],
        row["node_type"],
        row["security_level"],
        lat=row.get("lat"),
        lon=row.get("lon"),
        observed_at=row.get("observed_at"),
        properties={
            k: v
            for k, v in row.get("properties", {}).items()
            if k not in {"kg_node_id", "scenario_id", "entity_id", "label", "node_type", "security_level", "lat", "lon", "observed_at"}
        },
    )


def _edge(row: dict[str, Any]) -> KnowledgeEdge:
    return KnowledgeEdge(
        row["kg_edge_id"],
        row["scenario_id"],
        row["source_node_id"],
        row["target_node_id"],
        row["relation"],
        tuple(row["evidence_ids"]),
        row["security_level"],
        observed_at=row.get("observed_at"),
    )


def _evidence(row: dict[str, Any]) -> Evidence:
    return Evidence(
        row["evidence_id"],
        row["scenario_id"],
        row["evidence_type"],
        row["source_id"],
        row["text"],
        tuple(row["entity_ids"]),
        tuple(row["geo_object_ids"]),
        tuple(row["kg_edge_ids"]),
        row["document_id"],
        row["chunk_id"],
        row["security_level"],
    )


class Neo4jGraphStore:
    def __init__(self, config: Neo4jConfig) -> None:
        try:
            from neo4j import GraphDatabase  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "neo4j driver is required for the Neo4j backend (pip install neo4j)."
            ) from exc
        self._driver = GraphDatabase.driver(
            config.uri, auth=(config.user, config.password)
        )

    def close(self) -> None:
        self._driver.close()

    def _run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            return [record.data() for record in session.run(cypher, **params)]

    def neighborhood(
        self,
        entity_ids: tuple[str, ...],
        limit: int = 10,
        *,
        since: str | None = None,
        until: str | None = None,
        scenario_id: str | None = None,
    ) -> tuple[KnowledgeNode, ...]:
        rows = self._run(
            "MATCH (seed:KgNode) WHERE seed.entity_id IN $entity_ids "
            "AND ($scenario_id IS NULL OR seed.scenario_id = $scenario_id) "
            "OPTIONAL MATCH (seed)-[r:REL]-(nbr:KgNode) "
            "WHERE r IS NULL OR $scenario_id IS NULL OR r.scenario_id = $scenario_id "
            "WITH collect(DISTINCT seed) + collect(DISTINCT nbr) AS nodes "
            "UNWIND nodes AS n WITH DISTINCT n WHERE n IS NOT NULL "
            "AND ($scenario_id IS NULL OR n.scenario_id = $scenario_id) "
            "AND (n.observed_at IS NULL OR $since IS NULL OR "
            "     CASE WHEN n.observed_at CONTAINS 'T' THEN n.observed_at >= $since ELSE n.observed_at >= left($since, 10) END) "
            "AND (n.observed_at IS NULL OR $until IS NULL OR "
            "     CASE WHEN n.observed_at CONTAINS 'T' THEN n.observed_at <= $until ELSE n.observed_at <= left($until, 10) END) "
            "RETURN n.kg_node_id AS kg_node_id, n.scenario_id AS scenario_id, n.entity_id AS entity_id, "
            "n.label AS label, n.node_type AS node_type, n.security_level AS security_level, "
            "n.lat AS lat, n.lon AS lon, n.observed_at AS observed_at, properties(n) AS properties "
            "ORDER BY n.observed_at, n.kg_node_id LIMIT $limit",
            entity_ids=list(entity_ids),
            limit=limit,
            since=since,
            until=until,
            scenario_id=scenario_id,
        )
        return tuple(_node(row) for row in rows)

    def edges_for_nodes(
        self,
        node_ids: tuple[str, ...],
        limit: int = 20,
        *,
        since: str | None = None,
        until: str | None = None,
        scenario_id: str | None = None,
    ) -> tuple[KnowledgeEdge, ...]:
        rows = self._run(
            "MATCH (s:KgNode)-[r:REL]->(t:KgNode) "
            "WHERE (s.kg_node_id IN $node_ids OR t.kg_node_id IN $node_ids) "
            "AND ($scenario_id IS NULL OR r.scenario_id = $scenario_id) "
            "AND (r.observed_at IS NULL OR $since IS NULL OR "
            "     CASE WHEN r.observed_at CONTAINS 'T' THEN r.observed_at >= $since ELSE r.observed_at >= left($since, 10) END) "
            "AND (r.observed_at IS NULL OR $until IS NULL OR "
            "     CASE WHEN r.observed_at CONTAINS 'T' THEN r.observed_at <= $until ELSE r.observed_at <= left($until, 10) END) "
            "RETURN r.kg_edge_id AS kg_edge_id, r.scenario_id AS scenario_id, "
            "s.kg_node_id AS source_node_id, t.kg_node_id AS target_node_id, "
            "r.relation AS relation, r.evidence_ids AS evidence_ids, r.security_level AS security_level, "
            "r.observed_at AS observed_at ORDER BY r.observed_at, r.kg_edge_id LIMIT $limit",
            node_ids=list(node_ids),
            limit=limit,
            since=since,
            until=until,
            scenario_id=scenario_id,
        )
        return tuple(_edge(row) for row in rows)

    def evidence_for_edges(
        self, edge_ids: tuple[str, ...], *, scenario_id: str | None = None
    ) -> tuple[Evidence, ...]:
        rows = self._run(
            "MATCH (e:Evidence) WHERE any(x IN e.kg_edge_ids WHERE x IN $edge_ids) "
            "AND ($scenario_id IS NULL OR e.scenario_id = $scenario_id) "
            "RETURN e.evidence_id AS evidence_id, e.scenario_id AS scenario_id, e.evidence_type AS evidence_type, "
            "e.source_id AS source_id, e.text AS text, e.entity_ids AS entity_ids, e.geo_object_ids AS geo_object_ids, "
            "e.kg_edge_ids AS kg_edge_ids, e.document_id AS document_id, e.chunk_id AS chunk_id, "
            "e.security_level AS security_level",
            edge_ids=list(edge_ids),
            scenario_id=scenario_id,
        )
        return tuple(_evidence(row) for row in rows)

    def reset_demo_data(self) -> None:
        """Remove graph records populated by ``ingest``."""

        self._run("MATCH (n) WHERE n:KgNode OR n:Evidence DETACH DELETE n")

    def unit_entity_ids(
        self, *, scenario_id: str | None = None, side: str | None = None
    ) -> tuple[str, ...]:
        """Unit 노드의 entity_id 목록(검색 seed 확보용). side 는 splat 된 top-level 속성."""
        rows = self._run(
            "MATCH (n:KgNode {node_type:'Unit'}) "
            "WHERE ($scenario_id IS NULL OR n.scenario_id = $scenario_id) "
            "AND ($side IS NULL OR n.side = $side) "
            "RETURN DISTINCT n.entity_id AS entity_id",
            scenario_id=scenario_id,
            side=side,
        )
        return tuple(row["entity_id"] for row in rows)

    # ------------------------------------------------------------------
    # 실시간 적재 (원본 ingest_sample_data 의 MERGE 쿼리를 단건 헬퍼로 분리)
    # ------------------------------------------------------------------
    def merge_node(self, n: KnowledgeNode) -> None:
        self._run(
            "MERGE (x:KgNode {kg_node_id:$id}) SET x.scenario_id=$sc, x.entity_id=$eid, "
            "x.label=$label, x.node_type=$nt, x.security_level=$sl, "
            "x.lat=$lat, x.lon=$lon, x.observed_at=$ts, x += $props",
            id=n.kg_node_id,
            sc=n.scenario_id,
            eid=n.entity_id,
            label=n.label,
            nt=n.node_type,
            sl=n.security_level,
            lat=n.lat,
            lon=n.lon,
            ts=n.observed_at,
            props=n.properties,
        )

    def merge_edge(self, e: KnowledgeEdge) -> None:
        self._run(
            "MATCH (s:KgNode {kg_node_id:$src}), (t:KgNode {kg_node_id:$tgt}) "
            "MERGE (s)-[r:REL {kg_edge_id:$id}]->(t) SET r.scenario_id=$sc, r.relation=$rel, "
            "r.evidence_ids=$evid, r.security_level=$sl, r.observed_at=$ts",
            src=e.source_node_id,
            tgt=e.target_node_id,
            id=e.kg_edge_id,
            sc=e.scenario_id,
            rel=e.relation,
            evid=list(e.evidence_ids),
            sl=e.security_level,
            ts=e.observed_at,
        )

    def merge_evidence(self, ev: Evidence) -> None:
        self._run(
            "MERGE (x:Evidence {evidence_id:$id}) SET x.scenario_id=$sc, x.evidence_type=$et, x.source_id=$srcid, "
            "x.text=$text, x.entity_ids=$ents, x.geo_object_ids=$geos, x.kg_edge_ids=$edges, "
            "x.document_id=$doc, x.chunk_id=$chunk, x.security_level=$sl",
            id=ev.evidence_id,
            sc=ev.scenario_id,
            et=ev.evidence_type,
            srcid=ev.source_id,
            text=ev.text,
            ents=list(ev.entity_ids),
            geos=list(ev.geo_object_ids),
            edges=list(ev.kg_edge_ids),
            doc=ev.document_id,
            chunk=ev.chunk_id,
            sl=ev.security_level,
        )

    def ingest(
        self,
        nodes: Iterable[KnowledgeNode],
        edges: Iterable[KnowledgeEdge],
        evidences: Iterable[Evidence],
    ) -> None:
        """Load KG nodes, edges, and edge-linked evidence into Neo4j via MERGE."""
        for n in nodes:
            self.merge_node(n)
        for e in edges:
            self.merge_edge(e)
        for ev in evidences:
            self.merge_evidence(ev)
