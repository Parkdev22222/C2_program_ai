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
