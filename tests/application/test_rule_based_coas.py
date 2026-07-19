"""규칙기반 3-COA: 구별되는 3개 + validate 통과."""
from c2.application.agent.mission_planner import build_rule_based_coas
from c2.domain.planning.mission_plan import validate_mission_plan


def _state():
    units = [
        {"id": "보병1중대", "side": "BLUFOR", "unit_type": "기계화보병", "x": 7000, "y": 6000,
         "combat_power": 100.0, "status": "active", "color": "#1E88E5"},
        {"id": "전차중대", "side": "BLUFOR", "unit_type": "전차", "x": 6000, "y": 7000,
         "combat_power": 100.0, "status": "active", "color": "#00BCD4"},
        {"id": "적보병1중대", "side": "OPFOR", "unit_type": "기계화보병", "x": 20000, "y": 19000,
         "combat_power": 100.0, "status": "active", "color": "#E53935"},
    ]
    return {
        "units": units,
        "intelligence": {"BLUFOR": [
            {"unit_id": "적보병1중대", "status": "detected", "known_x": 20000, "known_y": 19000,
             "unit_type": "기계화보병", "combat_power": 100.0, "detected_by": "보병1중대"}]},
        "control_points": [
            {"id": "통제-알파", "x": 12000, "y": 14000, "owner": None},
            {"id": "통제-브라보", "x": 15000, "y": 15000, "owner": None},
            {"id": "통제-찰리", "x": 14000, "y": 12000, "owner": None}],
        "air_use_count": {"BLUFOR": 0}, "air_use_limit": 5,
    }


def test_three_distinct_valid_coas():
    coas = build_rule_based_coas(_state())
    assert len(coas) == 3
    ids = [c["id"] for c in coas]
    assert ids == ["COA1", "COA2", "COA3"]
    # 각 plan validate 통과
    for c in coas:
        validate_mission_plan(c["plan"])
        assert c["plan"]["mission_plans"], f"{c['id']} 비어있음"
    # 서로 다른 계획(최소 waypoint 목표가 다름)
    sig = [str(c["plan"]["mission_plans"]) for c in coas]
    assert len(set(sig)) == 3, "3개 COA가 서로 달라야 함"


def test_coas_have_labels_and_summary():
    coas = build_rule_based_coas(_state())
    for c in coas:
        assert c["label"] and c["doctrine"] and c["summary"]
