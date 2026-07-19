"""채팅 COA 수정 컨텍스트: pending COA가 있으면 응답에 coas 전달(무변경 시 미포함)."""
from c2.application.simulation.replan import _coa_chat_context


def test_coa_chat_context_lists_pending():
    coas = [{"id": "COA1", "label": "COA1 · 정면 집중", "summary": "s1",
             "plan": {"mission_plans": [{"company_id": "보병1중대"}]}}]
    ctx = _coa_chat_context(coas)
    assert "COA1" in ctx and "정면 집중" in ctx
    assert "수정" in ctx  # 수정 지시 포함


def test_coa_chat_context_empty():
    assert _coa_chat_context([]) == ""
