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
