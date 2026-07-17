"""Task 21/33: 시나리오 (c2.application.simulation.scenario).

- 시나리오 배치 함수(순수 함수, Unit 리스트 생성)는 애플리케이션 계층에 있다.
- 애플리케이션 시나리오 모듈은 domain + stdlib 외 아무것도 import 하지 않는다
  (infrastructure/tools/ui import 시 import-linter의 application-no-outward 계약 위반).
"""

import importlib
import inspect

from c2.domain.wargame.unit import Unit


def test_scenario_importable_from_application():
    mod = importlib.import_module("c2.application.simulation.scenario")
    assert hasattr(mod, "setup_cheorwon_bn")
    assert hasattr(mod, "setup_custom_scenario")
    # 영문 시나리오(setup_bn_vs_bn 계열)는 제거되었다.
    assert not hasattr(mod, "setup_bn_vs_bn")
    assert not hasattr(mod, "setup_bn_vs_bn_blufor_random")


def test_setup_cheorwon_bn_is_deterministic_unit_list():
    from c2.application.simulation.scenario import setup_cheorwon_bn

    units = setup_cheorwon_bn()
    assert len(units) == 12
    assert all(isinstance(u, Unit) for u in units)

    # 모든 부대명은 한국어 중대명 (아군·적군)
    ids = sorted(u.id for u in units)
    assert ids == sorted([
        "보병1중대", "보병2중대", "보병3중대", "전차중대", "대전차중대", "자주포중대",
        "적보병1중대", "적보병2중대", "적보병3중대", "적전차중대", "적대전차중대", "적자주포중대",
    ])
    assert all(not u.id.isascii() or "중대" in u.id for u in units), "영문 부대명이 남아있으면 안 됨"

    # 두 번 호출해도 동일한 결과 (비랜덤 고정 배치)
    units2 = setup_cheorwon_bn()
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
