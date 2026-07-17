"""[shim] 구현은 c2.domain.wargame.terrain 로 이동됨."""
from c2.domain.wargame.terrain import *  # noqa: F401,F403
from c2.domain.wargame.terrain import (  # noqa: F401
    get_heightmap,
    terrain,
    MAP_W,
    MAP_H,
    GRID_RES,
    GRID_W,
    GRID_H,
    KoreaRealTerrain,
    _FallbackTerrain,
    _load_terrain,
    _generate_fallback_heightmap,
)
