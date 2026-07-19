"""세션 pending_coas 상태."""
from c2.composition.container import build_session


def test_pending_coas_set_get_clear():
    s = build_session()
    assert s.pending_coas == []
    s.set_pending_coas([{"id": "COA1"}, {"id": "COA2"}])
    assert len(s.pending_coas) == 2
    s.clear_pending_coas()
    assert s.pending_coas == []
