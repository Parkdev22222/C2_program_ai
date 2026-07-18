"""온톨로지 확장: 아군 오사·적 포격 피해·적 이동 방향 반영 검증."""

from c2.application.ontology.wargame_builder import (
    WargameOntologyBuilder, _parse_event_actors, _heading_from_velocity,
)
from c2.application.ontology.coa_view import serialize_situation


def _state():
    return {
        "tick": 10, "game_time": 300.0,
        "units": [
            {"id": "보병1중대", "side": "BLUFOR", "unit_type": "기계화보병",
             "x": 10000, "y": 10000, "elevation": 100, "combat_power": 90.0,
             "status": "active", "current_action": "attack", "color": "blue",
             "velocity": [0.0, 0.0]},
            {"id": "자주포중대", "side": "BLUFOR", "unit_type": "자주포",
             "x": 9000, "y": 9000, "elevation": 110, "combat_power": 100.0,
             "status": "active", "current_action": "hold", "color": "blue",
             "velocity": [0.0, 0.0]},
            {"id": "보병2중대", "side": "BLUFOR", "unit_type": "기계화보병",
             "x": 9100, "y": 9100, "elevation": 105, "combat_power": 80.0,
             "status": "active", "current_action": "hold", "color": "blue",
             "velocity": [0.0, 0.0]},
            {"id": "적보병1중대", "side": "OPFOR", "unit_type": "기계화보병",
             "x": 16000, "y": 16000, "elevation": 150, "combat_power": 95.0,
             "status": "active", "current_action": "attack", "color": "red",
             "velocity": [-5.0, -5.0]},   # BLUFOR(남서) 방향으로 접근 중
            {"id": "적자주포중대", "side": "OPFOR", "unit_type": "자주포",
             "x": 22000, "y": 22000, "elevation": 200, "combat_power": 100.0,
             "status": "active", "current_action": "hold", "color": "red",
             "velocity": [0.0, 0.0]},
        ],
        "intelligence": {
            "BLUFOR": [
                {"unit_id": "적보병1중대", "status": "detected", "known_x": 16000,
                 "known_y": 16000, "unit_type": "기계화보병", "combat_power": 95.0,
                 "detected_by": "보병1중대"},
                {"unit_id": "적자주포중대", "status": "detected", "known_x": 22000,
                 "known_y": 22000, "unit_type": "자주포", "combat_power": 100.0,
                 "detected_by": "보병1중대"},
            ],
            "OPFOR": [],
        },
    }


_EVENTS = [
    # 아군 포격에 의한 아군 피해 (fratricide)
    {"id": 1, "tick": 10, "game_time": 300.0, "event_type": "FRATRICIDE_INDIRECT",
     "message": "자주포중대(자주포)⚠아군오사→보병2중대(기계화보병): -5.0% CP 누적 (AoE반경600m)"},
    # 아군 공습에 의한 아군 피해 (fratricide)
    {"id": 2, "tick": 10, "game_time": 300.0, "event_type": "FRATRICIDE_AIR",
     "message": "[BLUFOR] EAGLE-1⚠아군오사→보병2중대: -6.0% CP (거리0.1km)"},
    # 적 포격에 의한 아군 피해
    {"id": 3, "tick": 10, "game_time": 300.0, "event_type": "INDIRECT",
     "message": "적자주포중대(자주포) 간접사격 → 보병1중대: -8.0% CP 누적 (AoE반경600m, 정확도:detected)"},
]


def test_parse_fratricide_actors():
    assert _parse_event_actors(
        "FRATRICIDE_INDIRECT", "자주포중대(자주포)⚠아군오사→보병2중대(기계화보병): -5.0% CP") \
        == ("자주포중대", "보병2중대")
    assert _parse_event_actors(
        "FRATRICIDE_AIR", "[BLUFOR] EAGLE-1⚠아군오사→보병2중대: -6.0% CP") \
        == (None, "보병2중대")


def test_heading_from_velocity():
    lab, deg, spd, moving = _heading_from_velocity([-5.0, -5.0])
    assert lab == "남서" and moving is True and spd > 0
    lab2, _d, _s, moving2 = _heading_from_velocity([0.0, 0.0])
    assert lab2 is None and moving2 is False


def test_builder_reflects_fratricide_enemy_fire_and_movement():
    nodes, edges, evidences = WargameOntologyBuilder().build(_state(), _EVENTS)
    relations = {e.relation for e in edges}

    # 아군 오사(친화력) 엣지 — 아군 자주포 → 아군 보병
    ff = [e for e in edges if e.relation == "friendly_fire"]
    assert ff, "아군 포격 오사 friendly_fire 엣지가 있어야 함"
    assert ff[0].source_node_id == "KGN-UNIT-자주포중대"
    assert ff[0].target_node_id == "KGN-UNIT-보병2중대"

    # 적 포격에 의한 아군 피해 → 교전(engages) 엣지 (적자주포중대 → 보병1중대)
    eng = [e for e in edges if e.relation == "engages"]
    assert any(e.source_node_id == "KGN-UNIT-적자주포중대"
               and e.target_node_id == "KGN-UNIT-보병1중대" for e in eng)

    # 적 이동 방향 → advances_toward 엣지 (접근 중인 적보병1중대 → 최근접 아군)
    adv = [e for e in edges if e.relation == "advances_toward"]
    assert any(e.source_node_id == "KGN-UNIT-적보병1중대" for e in adv), \
        "이동 중 탐지 적의 접근 방향 advances_toward 엣지가 있어야 함"

    # 관측 노드에 이동 방향(heading) 반영
    obs_red = next(n for n in nodes if n.node_type == "Observation"
                   and n.entity_id == "적보병1중대")
    assert obs_red.properties["heading"] == "남서"
    assert obs_red.properties["moving"] is True

    # 3개 이벤트 모두 Event 노드로 생성 (fratricide 2 + 적 포격 1)
    event_nodes = [n for n in nodes if n.node_type == "Event"]
    assert len(event_nodes) == 3


def test_situation_surfaces_fratricide_and_advancing():
    from c2.infrastructure.ontology.factory import build_graph_store
    from c2.application.ontology.retrieval import retrieve_graph_context
    from c2.application.ontology.wargame_builder import WARGAME_SCENARIO_ID

    store = build_graph_store()
    store.ingest(*WargameOntologyBuilder().build(_state(), _EVENTS))
    seeds = store.unit_entity_ids(scenario_id=WARGAME_SCENARIO_ID, side="BLUFOR")
    kg_nodes, kg_edges, evid = retrieve_graph_context(
        store, seeds, scenario_id=WARGAME_SCENARIO_ID, node_limit=2000, edge_limit=4000)
    sit = serialize_situation(kg_nodes, kg_edges, evid)
    assert sit["summary"]["friendly_fire_incidents"] >= 1
    assert sit["summary"]["advancing_enemies"] >= 1
    rels = {r["relation"] for r in sit["force_relations"]}
    assert "friendly_fire" in rels and "advances_toward" in rels
