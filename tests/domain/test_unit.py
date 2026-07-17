def test_unit_and_airsupport_importable_from_domain():
    from c2.domain.wargame.unit import Unit, AirSupport
    assert hasattr(Unit, "effective_firepower")
    assert hasattr(Unit, "is_active")
    assert hasattr(Unit, "distance_to")
    assert hasattr(Unit, "to_dict")
    assert hasattr(Unit, "from_row")
    assert hasattr(AirSupport, "to_dict")
