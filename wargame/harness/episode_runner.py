"""[shim] 에피소드 실행 구현은 c2.application.harness.episode_runner 로 이동됨 (Task 26)."""

from c2.application.harness.episode_runner import (  # noqa: F401  [shim]
    EpisodeRunner,
    RuleBasedTactician,
)
