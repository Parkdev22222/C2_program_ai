"""[shim] 단일 도구 호출 가드는 c2.presentation.tools.single_tool_guard 로 이동됨 (Task 28)."""
from c2.presentation.tools.single_tool_guard import (  # noqa: F401  [shim]
    session_start,
    activate,
    deactivate,
    reset,
    guard,
)
