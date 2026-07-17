"""[shim] 워게임 정찰 임무 도구는 c2.presentation.tools.wargame_recon_tool 로 이동됨 (Task 28)."""
from c2.presentation.tools.wargame_recon_tool import (  # noqa: F401  [shim]
    register_wargame_engine,
    assess_recon_need,
    recommend_recon_routes,
)
