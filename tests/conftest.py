import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
for p in (_ROOT, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# smolagents는 Python 3.10+ 전용이라 일부 로컬/테스트 환경(예: 시스템 Python 3.9)에는
# 설치할 수 없다. `tools` 패키지의 `__init__.py`는 eager하게 `from smolagents import tool`을
# 수행하므로, smolagents가 없으면 `import tools`(및 `from tools.xxx import ...`)가 전부 깨진다.
# 실제 smolagents가 설치돼 있으면 아래 스텁은 개입하지 않는다.
# (동일 패턴이 tests/tool_trace_eval.py의 CLI 진입점에도 이미 존재함)
try:
    import smolagents  # noqa: F401
except ModuleNotFoundError:
    import types

    def _tool_stub(fn=None, **_kwargs):
        if fn is None:
            return lambda f: f
        return fn

    _smolagents_stub = types.ModuleType("smolagents")
    _smolagents_stub.tool = _tool_stub
    _smolagents_stub.Tool = type("Tool", (), {})
    _smolagents_stub.CodeAgent = None
    sys.modules["smolagents"] = _smolagents_stub

# ─────────────────────────────────────────────────────────────────────────
# Task 33: DI 기본 팩토리 wiring
#
# 과거에는 `wargame/engine.py`, `wargame/harness/__init__.py` 레거시 shim이
# import 시점에 `set_default_event_store_factory()` / `set_default_harness_db_factory()`
# 를 호출해 기본 팩토리를 전역 등록했다. 이제 테스트는 c2.* 를 직접 import하므로
# 그 shim import가 더 이상 일어나지 않는다 — db 를 명시적으로 주입하지 않는 소수의
# 테스트(engine/harness controller의 "기본 팩토리로 db 없이 생성" 동작 검증)를 위해
# 세션 전체에서 한 번 기본 팩토리를 임시 경로로 wiring한다 (프로덕션 wiring은
# `c2.composition.container.build_session()` 이 담당하며 이와 무관).
# ─────────────────────────────────────────────────────────────────────────
import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _wire_default_di_factories(tmp_path_factory):
    from c2.application.simulation.engine import set_default_event_store_factory
    from c2.infrastructure.persistence.sqlite_event_store import WargameDB
    from c2.application.harness.controller import set_default_harness_db_factory
    from c2.infrastructure.persistence.harness_db import HarnessDB

    db_dir = tmp_path_factory.mktemp("default_stores")
    set_default_event_store_factory(lambda: WargameDB(db_path=db_dir / "wargame_state.db"))
    set_default_harness_db_factory(lambda: HarnessDB(db_path=db_dir / "harness.db"))
