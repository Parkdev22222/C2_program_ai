"""Task 20: 엔진 → c2.application.simulation.engine + EventStore 포트 역전 검증.

- 애플리케이션 엔진은 인프라(c2.infrastructure)를 import 하지 않는다 (의존성 역전).
- 레거시 shim(wargame.engine)이 기본 WargameDB를 팩토리로 주입한다.
"""

import importlib
import inspect

import pytest


def test_engine_importable_from_application():
    mod = importlib.import_module("c2.application.simulation.engine")
    assert hasattr(mod, "WargameEngine")


def test_shim_identity():
    from c2.application.simulation.engine import WargameEngine as AppEngine
    from wargame.engine import WargameEngine as ShimEngine

    assert ShimEngine is AppEngine


def test_application_engine_source_has_no_infrastructure_import():
    mod = importlib.import_module("c2.application.simulation.engine")
    src = inspect.getsource(mod)
    assert "c2.infrastructure" not in src
    assert "WargameDB" not in src


def test_direct_construction_without_factory_raises(monkeypatch):
    """의존성 역전 증명: 팩토리 미설정 + db 생략 → RuntimeError."""
    engine_mod = importlib.import_module("c2.application.simulation.engine")
    monkeypatch.setattr(engine_mod, "_default_event_store_factory", None)
    with pytest.raises(RuntimeError):
        engine_mod.WargameEngine([])


def test_legacy_construction_uses_injected_default():
    """wargame.engine import 후 레거시 WargameEngine(units) (db 없이) 동작 + 1틱."""
    import wargame.engine  # noqa: F401  (팩토리 wiring 발생)
    from wargame.engine import WargameEngine
    from wargame.scenario import setup_bn_vs_bn

    units = setup_bn_vs_bn()
    eng = WargameEngine(units)  # db 미주입 → 기본 WargameDB 팩토리
    eng._tick()
    state = eng.get_state()
    assert state["units"]
