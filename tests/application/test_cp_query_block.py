"""공격계획 쿼리: 통제구역 목표 블록 구성."""
from c2.application.agent.mission_planner import _build_control_point_block


def test_control_point_block_contains_cp_info():
    state = {
        "control_points": [
            {"id": "통제-브라보", "x": 15000.0, "y": 15000.0, "radius": 2000.0,
             "owner": "BLUFOR", "blufor_near": 1, "opfor_near": 0},
        ],
    }
    block = _build_control_point_block(state)
    assert "통제구역" in block
    assert "통제-브라보" in block
    assert "15000" in block
    assert "2000" in block
    assert "확보" in block


def test_control_point_block_empty_when_no_cps():
    assert _build_control_point_block({"control_points": []}) == ""
    assert _build_control_point_block({}) == ""
