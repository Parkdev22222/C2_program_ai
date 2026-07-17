def test_new_module_exports_all_functions():
    from c2.domain.wargame.coordinates import (
        xy_to_latlon, latlon_to_xy, waypoints_xy_to_latlon,
        waypoints_latlon_to_xy, is_latlon_coords,
    )
    lat, lon = xy_to_latlon(0, 0)
    assert isinstance(lat, float) and isinstance(lon, float)

    x, y = latlon_to_xy(lat, lon)
    assert (x, y) == (0, 0)

    wps_ll = waypoints_xy_to_latlon([[100, 200], {"x": 300, "y": 400}])
    assert len(wps_ll) == 2

    wps_xy = waypoints_latlon_to_xy(wps_ll)
    assert len(wps_xy) == 2

    assert is_latlon_coords(wps_ll) is True
    assert is_latlon_coords([[100, 200]]) is False


def test_shim_points_to_same_object():
    from c2.domain.wargame.coordinates import xy_to_latlon as new_fn
    from tools.coord_utils import xy_to_latlon as shim_fn
    assert new_fn is shim_fn, "shim이 새 모듈을 재-export해야 함"

    from c2.domain.wargame.coordinates import (
        latlon_to_xy as new_latlon_to_xy,
        waypoints_xy_to_latlon as new_waypoints_xy_to_latlon,
        waypoints_latlon_to_xy as new_waypoints_latlon_to_xy,
        is_latlon_coords as new_is_latlon_coords,
    )
    from tools.coord_utils import (
        latlon_to_xy as shim_latlon_to_xy,
        waypoints_xy_to_latlon as shim_waypoints_xy_to_latlon,
        waypoints_latlon_to_xy as shim_waypoints_latlon_to_xy,
        is_latlon_coords as shim_is_latlon_coords,
    )
    assert new_latlon_to_xy is shim_latlon_to_xy
    assert new_waypoints_xy_to_latlon is shim_waypoints_xy_to_latlon
    assert new_waypoints_latlon_to_xy is shim_waypoints_latlon_to_xy
    assert new_is_latlon_coords is shim_is_latlon_coords
