def test_unit_and_airsupport_importable_from_domain():
    from c2.domain.wargame.unit import Unit, AirSupport
    assert hasattr(Unit, "effective_firepower")
    assert hasattr(Unit, "is_active")
    assert hasattr(Unit, "distance_to")
    assert hasattr(Unit, "to_dict")
    assert hasattr(Unit, "from_row")
    assert hasattr(AirSupport, "to_dict")


def test_models_shim_reexports_same_classes():
    from c2.domain.wargame.unit import Unit as NewUnit, AirSupport as NewAirSupport
    from wargame.models import Unit as ShimUnit, AirSupport as ShimAirSupport
    assert NewUnit is ShimUnit
    assert NewAirSupport is ShimAirSupport
