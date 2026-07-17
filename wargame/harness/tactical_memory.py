"""[shim] 전술 메모리 구현은 c2.application.harness.tactical_memory 로 이동됨 (Task 26)."""

from c2.application.harness.tactical_memory import (  # noqa: F401  [shim]
    TacticalMemory,
    SpatialRuleExtractor,
    get_tactical_memory,
    sample_terrain_profile,
    compute_context_similarity,
    TACTICAL_MEMORY_FILE,
)
