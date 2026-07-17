"""Task 31 — Gradio UI 삭제 검증.

목적:
  1. `ui/gradio_app.py` 파일 자체가 더 이상 존재하지 않음을 고정한다.
  2. `import ui.web_api`가 gradio 없이 성공함을 런타임으로 검증한다
     (Task 30에서 web_api가 gradio 비의존이 됐음을 재확인).
  3. 테스트/gradio-부재 단언 파일을 제외한 나머지 소스 파일 어디에도
     `ui.gradio_app`에 대한 실제 런타임 import 구문이 남아있지 않음을
     소스 검사로 고정한다 (주석/docstring의 역사적 언급은 허용).
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent

# 이 파일들은 "gradio_app이 없음"을 확인하는 단언(assert ... not in src) 목적으로
# 문자열 "import ui.gradio_app" 등을 포함하므로 실제 런타임 import가 아니다.
_ALLOWED_FILES = {
    _ROOT / "tests" / "presentation" / "test_web_api_wiring.py",
    _ROOT / "tests" / "presentation" / "test_gradio_removed.py",
}

_LIVE_IMPORT_PATTERNS = [
    re.compile(r"^\s*from\s+ui\.gradio_app\s+import\b", re.MULTILINE),
    re.compile(r"^\s*from\s+\.gradio_app\s+import\b", re.MULTILINE),
    re.compile(r"^\s*import\s+ui\.gradio_app\b", re.MULTILINE),
    re.compile(r"^\s*from\s+ui\s+import\s+gradio_app\b", re.MULTILINE),
]


def test_gradio_app_file_does_not_exist():
    assert not (_ROOT / "ui" / "gradio_app.py").exists(), (
        "ui/gradio_app.py 가 아직 삭제되지 않았습니다"
    )


def test_import_ui_web_api_succeeds_without_gradio():
    """gradio가 설치되어 있지 않은 이 환경에서 ui.web_api import가 성공해야 한다."""
    import importlib
    import sys

    sys.modules.pop("ui.web_api", None)
    mod = importlib.import_module("ui.web_api")
    assert hasattr(mod, "app") or hasattr(mod, "start_server")


def test_no_live_gradio_app_import_remains():
    """테스트/단언 파일을 제외한 모든 .py 파일에 gradio_app의 실제 런타임 import가 없어야 한다."""
    offenders = []
    for py_file in _ROOT.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        if ".superpowers" in py_file.parts:
            continue
        if py_file in _ALLOWED_FILES:
            continue
        if py_file.name == "gradio_app.py":
            # gradio_app.py 자신은 삭제 대상이며, 존재해서는 안 된다
            # (test_gradio_app_file_does_not_exist에서 별도 검증).
            continue
        try:
            src = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in _LIVE_IMPORT_PATTERNS:
            if pattern.search(src):
                offenders.append(str(py_file.relative_to(_ROOT)))
                break
    assert not offenders, f"gradio_app에 대한 실제 런타임 import가 남아있습니다: {offenders}"
