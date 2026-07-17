def test_map_max_and_validator_from_domain():
    from c2.domain.planning.mission_plan import MAP_MAX, validate_mission_plan
    assert MAP_MAX == 30_000.0
    result = validate_mission_plan({"mission_plans": []})
    assert isinstance(result, dict)


def test_validator_shim_reexports_same_callable():
    from c2.domain.planning.mission_plan import validate_mission_plan as new_fn
    from tools.mission_plan_validator import validate_mission_plan as shim_fn
    assert new_fn is shim_fn
