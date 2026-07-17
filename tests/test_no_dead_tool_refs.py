"""Test to verify dead tool references are removed."""
from pathlib import Path

# 레거시 agent/battlefield_agent.py shim은 Task 34에서 삭제됨 —
# 실제 구현이 이전된 canonical 경로(src/c2)를 검사한다.
_AGENT = (
    Path(__file__).resolve().parent.parent
    / "src" / "c2" / "presentation" / "agent" / "battlefield_agent.py"
)


def test_no_videodb_or_pdf_rag_imports():
    """Assert that videodb_query_tool and pdf_rag_tool references are absent."""
    src = _AGENT.read_text(encoding="utf-8")
    assert "videodb_query_tool" not in src
    assert "pdf_rag_tool" not in src
