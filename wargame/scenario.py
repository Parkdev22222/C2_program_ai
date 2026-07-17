"""[shim] 워게임 시나리오 정의는 c2.application.simulation.scenario 로 이동됨 (Task 21).

이 모듈은 하위 호환을 위한 순수 재노출(shim)이며 네이티브 구현은 없다.
"""

from c2.application.simulation.scenario import (  # noqa: F401  [shim]
    setup_bn_vs_bn,
    setup_cheorwon_bn,
    setup_custom_scenario,
    setup_bn_vs_bn_blufor_random,
    get_unit_type,
    _pick_pos,
    _BLUFOR_ZONE,
    _OPFOR_ZONE,
    _MIN_SEP,
    UNIT_TYPE_SPECS,
    UNIT_TYPE_LABEL,
)
