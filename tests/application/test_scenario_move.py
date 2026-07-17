"""Task 21: 시나리오 → c2.application.simulation.scenario + shim.

- 시나리오 배치 함수(순수 함수, Unit 리스트 생성)는 애플리케이션 계층으로 이동.
- 레거시 shim(wargame.scenario)은 항등 재노출만 수행한다.
- 애플리케이션 시나리오 모듈은 domain + stdlib 외 아무것도 import 하지 않는다
  (infrastructure/tools/ui import 시 import-linter의 application-no-outward 계약 위반).
"""

import importlib
import inspect

from c2.domain.wargame.unit import Unit


def test_scenario_importable_from_application():
    mod = importlib.import_module("c2.application.simulation.scenario")
    assert hasattr(mod, "setup_bn_vs_bn")
    assert hasattr(mod, "setup_cheorwon_bn")
    assert hasattr(mod, "setup_custom_scenario")
    assert hasattr(mod, "setup_bn_vs_bn_blufor_random")


def test_shim_identity_for_all_public_functions():
    import wargame.scenario as shim_mod
    import c2.application.simulation.scenario as app_mod

    for name in (
        "setup_bn_vs_bn",
        "setup_cheorwon_bn",
        "setup_custom_scenario",
        "setup_bn_vs_bn_blufor_random",
        "get_unit_type",
        "_pick_pos",
    ):
        assert getattr(shim_mod, name) is getattr(app_mod, name), (
            f"{name} 이 shim과 application 모듈에서 동일 객체가 아님"
        )

    for name in (
        "_BLUFOR_ZONE",
        "_OPFOR_ZONE",
        "_MIN_SEP",
        "UNIT_TYPE_SPECS",
        "UNIT_TYPE_LABEL",
    ):
        assert getattr(shim_mod, name) is getattr(app_mod, name), (
            f"{name} 상수가 shim과 application 모듈에서 동일 객체가 아님"
        )


def test_setup_bn_vs_bn_is_deterministic_unit_list():
    from c2.application.simulation.scenario import setup_bn_vs_bn

    units = setup_bn_vs_bn()
    assert len(units) == 10
    assert all(isinstance(u, Unit) for u in units)

    ids = sorted(u.id for u in units)
    assert ids == sorted(
        ["Charlie", "Alpha", "Bravo", "Delta", "Echo", "Red1", "Red2", "Red3", "Red4", "Red5"]
    )

    # 두 번 호출해도 동일한 결과 (비랜덤 고정 배치)
    units2 = setup_bn_vs_bn()
    snap1 = sorted((u.id, u.x, u.y, u.combat_power, u.firepower_index) for u in units)
    snap2 = sorted((u.id, u.x, u.y, u.combat_power, u.firepower_index) for u in units2)
    assert snap1 == snap2


def test_application_scenario_module_has_no_outward_imports():
    mod = importlib.import_module("c2.application.simulation.scenario")
    src = inspect.getsource(mod)
    for forbidden in (
        "c2.infrastructure",
        "c2.presentation",
        "import wargame",
        "from wargame",
        "import tools",
        "from tools",
        "import ui",
        "from ui",
    ):
        assert forbidden not in src, f"application scenario 모듈에 금지된 import 발견: {forbidden}"
