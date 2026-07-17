def test_map_max_and_validator_from_domain():
    from c2.domain.planning.mission_plan import MAP_MAX, validate_mission_plan
    assert MAP_MAX == 30_000.0
    result = validate_mission_plan({"mission_plans": []})
    assert isinstance(result, dict)
