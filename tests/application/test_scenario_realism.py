"""현실성 튜닝: 속도 상향·배치 밀도·포병 사거리 검증."""

from c2.application.simulation.scenario import (
    setup_cheorwon_bn, UNIT_TYPE_SPECS, _BLUFOR_ZONE, _OPFOR_ZONE,
)

_EXPECTED_SPEED = {"전차": 6.0, "기계화보병": 5.0, "대전차": 5.5, "자주포": 4.0}


def test_unit_type_specs_speeds_raised():
    assert UNIT_TYPE_SPECS["전차"]["max_speed"] == 6.0
    assert UNIT_TYPE_SPECS["기계화보병"]["max_speed"] == 5.0
    assert UNIT_TYPE_SPECS["대전차"]["max_speed"] == 5.5
    assert UNIT_TYPE_SPECS["자주포"]["max_speed"] == 4.0
    assert UNIT_TYPE_SPECS["정찰"]["max_speed"] == 7.0


def test_scenario_unit_speeds_match_type():
    for u in setup_cheorwon_bn():
        assert u.max_speed == _EXPECTED_SPEED[u.unit_type], u.id


def test_zones_shrunk_to_battalion_frontage():
    assert _BLUFOR_ZONE == dict(x_min=5_000, x_max=10_000, y_min=5_000, y_max=10_000)
    assert _OPFOR_ZONE == dict(x_min=18_000, x_max=23_000, y_min=18_000, y_max=23_000)


def test_units_start_inside_new_zones():
    for u in setup_cheorwon_bn():
        z = _BLUFOR_ZONE if u.side == "BLUFOR" else _OPFOR_ZONE
        assert z["x_min"] <= u.x <= z["x_max"], u.id
        assert z["y_min"] <= u.y <= z["y_max"], u.id


def test_artillery_indirect_range_map_scaled():
    units = {u.id: u for u in setup_cheorwon_bn()}
    assert units["자주포중대"].indirect_range == 15_000.0
    assert units["적자주포중대"].indirect_range == 18_000.0
