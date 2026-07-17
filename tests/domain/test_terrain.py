def test_terrain_public_api_from_domain():
    from c2.domain.wargame import terrain

    hm = terrain.get_heightmap()
    assert hm is not None
    assert hm.shape == (terrain.GRID_H, terrain.GRID_W)

    # instance API used by engine combat/detection calculations
    e = terrain.terrain.elevation(1000, 1000)
    assert isinstance(e, float)
    adv = terrain.terrain.elevation_advantage(1000, 1000, 2000, 2000)
    assert isinstance(adv, float)
    cov = terrain.terrain.cover_factor(1000, 1000)
    assert isinstance(cov, float)
    spd = terrain.terrain.movement_speed_factor(1000, 1000)
    assert isinstance(spd, float)


def test_terrain_shim_reexports():
    from c2.domain.wargame.terrain import get_heightmap as new_fn
    from wargame.terrain import get_heightmap as shim_fn
    assert new_fn is shim_fn

    from c2.domain.wargame.terrain import terrain as new_terrain
    from wargame.terrain import terrain as shim_terrain
    assert new_terrain is shim_terrain


def test_terrain_korea_shim_reexports():
    from c2.domain.wargame.terrain import KoreaRealTerrain as new_cls
    from wargame.terrain_korea import KoreaRealTerrain as shim_cls
    assert new_cls is shim_cls
