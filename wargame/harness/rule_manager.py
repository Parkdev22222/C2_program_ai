"""[shim] 규칙 관리 구현은 c2.application.harness.rule_manager 로 이동됨 (Task 26)."""

from c2.application.harness.rule_manager import (  # noqa: F401  [shim]
    RuleManager,
    SECTIONS,
    INSTRUCTIONS_FILE,
)
