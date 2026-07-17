"""[shim] OPFOR 이동경로 예측 도구는 c2.presentation.tools.wargame_opfor_routes_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.wargame_opfor_routes_tool import (  # noqa: F401  [shim]
    register_wargame_engine,
    predict_opfor_routes,
)
