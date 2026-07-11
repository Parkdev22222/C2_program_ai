"""온톨로지 파이프라인 테스트 (Neo4j 없이 in-memory 폴백으로 종단 검증).

워게임 상태 → 동일 스키마 KG 변환 → 적재 → 검색(neighborhood/edges/evidence) →
COA 상황 직렬화까지 검증한다. smolagents 등 무거운 의존성 없이 실행된다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ontology.coa_view import serialize_situation
from ontology.factory import build_graph_store
from ontology.retrieval import retrieve_graph_context
from ontology.wargame_builder import WARGAME_SCENARIO_ID, WargameOntologyBuilder


def _state(tick, gt, alpha_x, alpha_cp):
    return {
        "tick": tick,
        "game_time": gt,
        "units": [
            {"id": "Alpha", "side": "BLUFOR", "unit_type": "기계화보병",
             "x": alpha_x, "y": 4000, "elevation": 120, "combat_power": alpha_cp,
             "status": "active", "current_action": "move", "color": "blue"},
            {"id": "Bravo", "side": "BLUFOR", "unit_type": "전차",
             "x": 6000, "y": 4200, "elevation": 130, "combat_power": 88.0,
             "status": "active", "current_action": "attack", "color": "blue"},
            {"id": "Red1", "side": "OPFOR", "unit_type": "전차",
             "x": 20000, "y": 21000, "elevation": 200, "combat_power": 90.0,
             "status": "active", "current_action": "hold", "color": "red"},
        ],
        "intelligence": {
            "BLUFOR": [{"unit_id": "Red1", "status": "detected",
                        "known_x": 20000, "known_y": 21000, "unit_type": "전차",
                        "combat_power": 90.0, "detected_by": "Alpha"}],
            "OPFOR": [],
        },
    }


_EVENTS = [
    {"id": 1, "tick": 9, "game_time": 290.0, "event_type": "COMBAT",
     "message": "Red1(전차)→Bravo(전차): 직사 교전 피해 12"},
    {"id": 2, "tick": 8, "game_time": 280.0, "event_type": "AIR_STRIKE",
     "message": "[OPFOR] EAGLE-1→Alpha: 공중폭격 피해 8"},
]


def test_fallback_store_when_no_neo4j(monkeypatch):
    monkeypatch.delenv("OI_NEO4J_URI", raising=False)
    store = build_graph_store()
    assert type(store).__name__ == "InMemoryGraphStore"


def test_build_ingest_retrieve_serialize():
    store = build_graph_store()
    builder = WargameOntologyBuilder()

    store.ingest(*builder.build(_state(10, 300.0, 5000, 95.0), _EVENTS))
    # 두 번째 스냅샷: Alpha 이동/CP 감소, 같은 이벤트 → Event 중복 없어야
    n2, e2, ev2 = builder.build(_state(20, 600.0, 8000, 70.0), _EVENTS)
    store.ingest(n2, e2, ev2)
    assert not any(x.node_type == "Event" for x in n2)

    seeds = store.unit_entity_ids(scenario_id=WARGAME_SCENARIO_ID, side="BLUFOR")
    assert set(seeds) == {"Alpha", "Bravo"}

    kg_nodes, kg_edges, evidences = retrieve_graph_context(
        store, seeds, scenario_id=WARGAME_SCENARIO_ID, node_limit=1000, edge_limit=2000
    )

    node_types = {n.node_type for n in kg_nodes}
    relations = {e.relation for e in kg_edges}
    assert {"Unit", "Observation", "Event"} <= node_types
    assert {"has_observation", "observes", "participates_in"} <= relations
    assert evidences

    sit = serialize_situation(kg_nodes, kg_edges, evidences)
    assert sit["summary"]["blufor_units"] == 2
    assert sit["summary"]["detected_targets"] == 1
    # 최신 관측(두 번째 스냅샷)의 CP 가 반영되어야 함
    alpha = next(u for u in sit["units"] if u["unit_id"] == "Alpha")
    assert alpha["combat_power"] == 70.0


def test_schema_fields_match_reference():
    """KG 노드/엣지/근거가 원본 스키마 필드를 갖는지 확인."""
    from ontology.models import Evidence, KnowledgeEdge, KnowledgeNode

    n, e, ev = WargameOntologyBuilder().build(_state(1, 60.0, 5000, 95.0), _EVENTS)
    assert all(isinstance(x, KnowledgeNode) for x in n)
    assert all(isinstance(x, KnowledgeEdge) for x in e)
    assert all(isinstance(x, Evidence) for x in ev)

    event_node = next(x for x in n if x.node_type == "Event")
    # Event 노드 properties 는 BattleEvent 페이로드(원본 _event_node_properties 규칙)
    for key in ("event_id", "event_type", "fatalities", "latitude", "longitude", "node_type", "title"):
        assert key in event_node.properties


if __name__ == "__main__":
    import types

    class _MP:
        def delenv(self, k, raising=True):
            os.environ.pop(k, None)

    test_fallback_store_when_no_neo4j(_MP())
    test_build_ingest_retrieve_serialize()
    test_schema_fields_match_reference()
    print("✅ ontology pipeline tests passed")
