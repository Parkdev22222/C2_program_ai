"""Task 20/33: 엔진 (c2.application.simulation.engine) + EventStore 포트 역전 검증.

- 애플리케이션 엔진은 인프라(c2.infrastructure)를 import 하지 않는다 (의존성 역전).
- 기본 WargameDB 팩토리는 tests/conftest.py 의 세션 autouse fixture 가 주입한다
  (Task 33: 레거시 shim import 소비 제거 이후 default factory wiring을 conftest 로 이관).
"""

import importlib
import inspect

import pytest


def test_engine_importable_from_application():
    mod = importlib.import_module("c2.application.simulation.engine")
    assert hasattr(mod, "WargameEngine")


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


def test_construction_uses_conftest_injected_default_factory():
    """tests/conftest.py 의 autouse fixture 가 주입한 기본 팩토리로 db 미주입 WargameEngine(units)
    생성 + 1틱이 동작함을 확인 (Task 33: 레거시 wargame.engine shim import 없이)."""
    from c2.application.simulation.engine import WargameEngine
    from c2.application.simulation.scenario import setup_bn_vs_bn

    units = setup_bn_vs_bn()
    eng = WargameEngine(units)  # db 미주입 → conftest에서 wiring한 기본 WargameDB 팩토리
    eng._tick()
    state = eng.get_state()
    assert state["units"]
