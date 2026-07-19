"""COA 프리뷰: 계획(미터) → 위경도 routes/air."""
from c2.application.simulation.replan import build_coa_preview


def _state():
    return {"units": [
        {"id": "보병1중대", "side": "BLUFOR", "x": 8000, "y": 8000, "color": "#1E88E5"},
    ]}


def test_build_coa_preview_converts_to_latlon():
    plan = {
        "mission_plans": [
            {"company_id": "보병1중대", "mission_type": "attack",
             "waypoints": [[12000, 12000], [15000, 15000]]},
        ],
        "air_support_plans": [
            {"call_sign": "EAGLE-1", "support_type": "cas",
             "target": [15000, 15000], "radius": 1500},
        ],
    }
    pv = build_coa_preview(plan, _state())
    assert len(pv["routes"]) == 1
    r = pv["routes"][0]
    assert r["unit_id"] == "보병1중대"
    # 현위치(8000,8000) + waypoint 2개 = 3점, 각 [lat,lon]
    assert len(r["latlon"]) == 3
    assert all(len(p) == 2 for p in r["latlon"])
    assert pv["air_support"][0]["call_sign"] == "EAGLE-1"
    assert len(pv["air_support"][0]["target"]) == 2
    assert pv["air_support"][0]["radius"] == 1500


def test_build_coa_preview_empty():
    pv = build_coa_preview({"mission_plans": []}, _state())
    assert pv["routes"] == [] and pv["air_support"] == []
