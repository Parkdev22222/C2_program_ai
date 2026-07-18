"""웹 API state 변환: control_points에 lat/lon 부여."""
from c2.presentation.web.api import _convert_state_to_api


def test_control_points_get_latlon():
    state = {
        "tick": 1, "game_time": 30.0, "game_time_str": "00:00:30",
        "units": [], "running": True, "winner": None,
        "intelligence": {"BLUFOR": [], "OPFOR": []}, "air_supports": [],
        "control_points": [
            {"id": "통제-브라보", "x": 15000.0, "y": 15000.0,
             "owner": "BLUFOR", "blufor_near": 1, "opfor_near": 0},
        ],
    }
    api = _convert_state_to_api(state)
    cps = api.get("control_points", [])
    assert len(cps) == 1
    assert "lat" in cps[0] and "lon" in cps[0]
    assert cps[0]["id"] == "통제-브라보"
    assert cps[0]["owner"] == "BLUFOR"
