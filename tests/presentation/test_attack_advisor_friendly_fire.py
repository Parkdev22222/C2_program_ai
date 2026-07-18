"""공격위치 툴: 아군 오사 위험 플래그 헬퍼."""

from c2.presentation.tools.wargame_attack_advisor_tool import _friendly_fire_risk


def test_friendly_fire_risk_in_blast():
    blufor = [{"id": "보병1중대", "x": 10_000, "y": 10_000},
              {"id": "전차중대",  "x": 13_000, "y": 10_000}]
    # cas 반경 1500 → 보병1중대(500m) 위험, 전차중대(2500m) 안전
    r = _friendly_fire_risk("cas", 10_500, 10_000, blufor)
    assert r["blast_radius_m"] == 1_500
    assert r["in_blast"] is True
    ids = [e["unit_id"] for e in r["endangered_units"]]
    assert "보병1중대" in ids and "전차중대" not in ids
    assert r["endangered_units"][0]["dist_m"] == 500


def test_friendly_fire_risk_clear():
    blufor = [{"id": "보병1중대", "x": 10_000, "y": 10_000}]
    r = _friendly_fire_risk("strike", 15_000, 15_000, blufor)  # 반경 400, 멀리
    assert r["in_blast"] is False
    assert r["endangered_units"] == []


def test_friendly_fire_risk_artillery_radius():
    blufor = [{"id": "보병1중대", "x": 10_000, "y": 10_000}]
    # artillery 반경 2500 → 2000m 떨어진 아군 위험
    r = _friendly_fire_risk("artillery", 12_000, 10_000, blufor)
    assert r["blast_radius_m"] == 2_500
    assert r["in_blast"] is True
