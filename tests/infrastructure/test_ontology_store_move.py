"""Task 16/33: 온톨로지 스토어 — c2.infrastructure.ontology.

- 새 경로(c2.infrastructure.ontology.*)에서 Neo4jGraphStore/InMemoryGraphStore/
  build_graph_store 가 임포트 가능한지 확인.
- InMemoryGraphStore 가 OntologyStore 포트를 여전히 구조적으로(isinstance) 만족하는지,
  merge_node → neighborhood/unit_entity_ids 라운드트립이 동작하는지 확인.
- neo4j 미접속 환경에서 build_graph_store() 가 in-memory 스토어로 폴백하는지 확인.
"""


def test_stores_importable_from_new_path():
    from c2.infrastructure.ontology.graph_store import Neo4jGraphStore, Neo4jConfig
    from c2.infrastructure.ontology.in_memory_store import InMemoryGraphStore
    from c2.infrastructure.ontology.factory import build_graph_store

    assert Neo4jGraphStore is not None
    assert Neo4jConfig is not None
    assert InMemoryGraphStore is not None
    assert build_graph_store is not None


def test_in_memory_store_satisfies_ontology_store_port():
    from c2.application.ports.ontology_store import OntologyStore
    from c2.infrastructure.ontology.in_memory_store import InMemoryGraphStore

    store = InMemoryGraphStore()
    assert isinstance(store, OntologyStore)


def test_in_memory_store_round_trip():
    from c2.domain.ontology.models import KnowledgeNode
    from c2.infrastructure.ontology.in_memory_store import InMemoryGraphStore

    store = InMemoryGraphStore()
    node = KnowledgeNode(
        "kg-node-1",
        "test-scenario",
        "Alpha",
        "Alpha 중대",
        "Unit",
        "UNCLASS",
    )
    store.merge_node(node)

    neighborhood = store.neighborhood(("Alpha",), scenario_id="test-scenario")
    assert node in neighborhood

    seeds = store.unit_entity_ids(scenario_id="test-scenario")
    assert seeds == ("Alpha",)


def test_build_graph_store_falls_back_to_in_memory(monkeypatch):
    monkeypatch.delenv("OI_NEO4J_URI", raising=False)
    from c2.infrastructure.ontology.factory import build_graph_store
    from c2.infrastructure.ontology.in_memory_store import InMemoryGraphStore

    store = build_graph_store()
    assert isinstance(store, InMemoryGraphStore)
