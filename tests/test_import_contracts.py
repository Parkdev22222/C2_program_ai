"""import-linter 계약(.importlinter)이 항상 통과하는지 검증.

`lint-imports` 콘솔 스크립트는 이 환경에서 PATH에 없을 수 있으므로,
`importlinter.cli.lint_imports_command`를 서브프로세스에서 직접 호출한다
(작업 지시서에 명시된 대체 실행 방법과 동일).
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_LINT_INVOCATION = (
    "from importlinter.cli import lint_imports_command; "
    "import sys; sys.argv=['lint-imports']; "
    "sys.exit(lint_imports_command.main(standalone_mode=False) or 0)"
)


def test_import_linter_contracts_all_kept():
    env_pythonpath = str(REPO_ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", _LINT_INVOCATION],
        cwd=str(REPO_ROOT),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": env_pythonpath},
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert "broken" in output, f"unexpected lint-imports output:\n{output}"
    assert "0 broken" in output, (
        f"import-linter contract(s) broken (return code {result.returncode}):\n{output}"
    )
    assert result.returncode == 0, f"lint-imports exited non-zero:\n{output}"
