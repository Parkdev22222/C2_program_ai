"""Test to verify dead tool references are removed."""
from pathlib import Path

_AGENT = Path(__file__).resolve().parent.parent / "agent" / "battlefield_agent.py"


def test_no_videodb_or_pdf_rag_imports():
    """Assert that videodb_query_tool and pdf_rag_tool references are absent."""
    src = _AGENT.read_text(encoding="utf-8")
    assert "videodb_query_tool" not in src
    assert "pdf_rag_tool" not in src
