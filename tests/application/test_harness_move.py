"""Task 26: 하네스(학습/평가) → c2.application.harness + harness_db → infra + shim 검증.

- 오케스트레이션(controller/episode_runner/metrics/rule_extractor/rule_manager/
  tactical_memory)은 c2.application.harness 로 이동.
- SQLite 영속 저장(HarnessDB)은 c2.infrastructure.persistence.harness_db 로 이동.
- 애플리케이션 하네스는 인프라(HarnessDB)를 런타임에 import 하지 않는다 —
  controller의 HarnessDB 의존은 DI 팩토리(Task 20의 EventStore 패턴과 동일)로 역전.
- 인프라 harness_db는 애플리케이션(EpisodeMetrics)을 런타임에 import 하지 않는다 —
  TYPE_CHECKING 가드만 사용 (타입힌트 전용).
- 레거시 shim(wargame.harness.*)은 항등 재노출 + 기본 HarnessDB 팩토리 주입을 수행한다.
"""

import importlib
import inspect

import pytest


# ── (a) 애플리케이션/인프라 위치에서 import 가능 ──────────────────────────

def test_harness_db_importable_from_infrastructure():
    mod = importlib.import_module("c2.infrastructure.persistence.harness_db")
    assert hasattr(mod, "HarnessDB")


def test_orchestration_importable_from_application():
    metrics_mod = importlib.import_module("c2.application.harness.metrics")
    assert hasattr(metrics_mod, "EpisodeMetrics")
    assert hasattr(metrics_mod, "collect_metrics")

    runner_mod = importlib.import_module("c2.application.harness.episode_runner")
    assert hasattr(runner_mod, "EpisodeRunner")
    assert hasattr(runner_mod, "RuleBasedTactician")

    extractor_mod = importlib.import_module("c2.application.harness.rule_extractor")
    assert hasattr(extractor_mod, "RuleExtractor")

    manager_mod = importlib.import_module("c2.application.harness.rule_manager")
    assert hasattr(manager_mod, "RuleManager")
    assert hasattr(manager_mod, "SECTIONS")

    tm_mod = importlib.import_module("c2.application.harness.tactical_memory")
    assert hasattr(tm_mod, "TacticalMemory")
    assert hasattr(tm_mod, "SpatialRuleExtractor")
    assert hasattr(tm_mod, "get_tactical_memory")

    controller_mod = importlib.import_module("c2.application.harness.controller")
    assert hasattr(controller_mod, "HarnessController")
    assert hasattr(controller_mod, "set_default_harness_db_factory")


# ── (b) shim 항등성 ────────────────────────────────────────────────────

def test_shim_identity_for_all_public_symbols():
    import wargame.harness as shim_pkg

    from c2.application.harness.metrics import EpisodeMetrics as AppEpisodeMetrics
    from c2.application.harness.episode_runner import EpisodeRunner as AppEpisodeRunner
    from c2.application.harness.rule_extractor import RuleExtractor as AppRuleExtractor
    from c2.application.harness.rule_manager import RuleManager as AppRuleManager
    from c2.application.harness.controller import HarnessController as AppController
    from c2.application.harness.tactical_memory import (
        TacticalMemory as AppTacticalMemory,
        SpatialRuleExtractor as AppSpatialRuleExtractor,
        get_tactical_memory as app_get_tactical_memory,
    )
    from c2.infrastructure.persistence.harness_db import HarnessDB as InfraHarnessDB

    assert shim_pkg.EpisodeMetrics is AppEpisodeMetrics
    assert shim_pkg.EpisodeRunner is AppEpisodeRunner
    assert shim_pkg.RuleExtractor is AppRuleExtractor
    assert shim_pkg.RuleManager is AppRuleManager
    assert shim_pkg.HarnessController is AppController
    assert shim_pkg.TacticalMemory is AppTacticalMemory
    assert shim_pkg.SpatialRuleExtractor is AppSpatialRuleExtractor
    assert shim_pkg.get_tactical_memory is app_get_tactical_memory
    assert shim_pkg.HarnessDB is InfraHarnessDB

    import wargame.harness.tactical_memory as shim_tm
    assert shim_tm.get_tactical_memory is app_get_tactical_memory
    assert shim_tm.sample_terrain_profile is (
        importlib.import_module("c2.application.harness.tactical_memory").sample_terrain_profile
    )

    import wargame.harness.harness_db as shim_db
    assert shim_db.HarnessDB is InfraHarnessDB


# ── (c) 애플리케이션 하네스 소스에 인프라/레거시 import 없음 ─────────────

def _lines_outside_type_checking(src: str):
    """TYPE_CHECKING 블록(들여쓰기) 밖의 코드 라인만 반환한다.

    controller.py / rule_manager.py는 HarnessDB 타입힌트를 위해
    `if TYPE_CHECKING: from c2.infrastructure... import HarnessDB` 를
    사용한다 — 이는 런타임에 실행되지 않으므로 layering 위반이 아니다.
    """
    lines = src.splitlines()
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("if TYPE_CHECKING"):
            in_block = True
            continue
        if in_block:
            if line.startswith(" ") or line.startswith("\t"):
                continue  # TYPE_CHECKING 블록 내부 (허용됨)
            in_block = False
        yield line


@pytest.mark.parametrize(
    "module_name",
    [
        "c2.application.harness.metrics",
        "c2.application.harness.episode_runner",
        "c2.application.harness.rule_extractor",
        "c2.application.harness.rule_manager",
        "c2.application.harness.tactical_memory",
        "c2.application.harness.controller",
    ],
)
def test_application_harness_module_has_no_infra_or_legacy_import(module_name):
    mod = importlib.import_module(module_name)
    src = inspect.getsource(mod)
    runtime_src = "\n".join(_lines_outside_type_checking(src))
    for forbidden in (
        "c2.infrastructure",
        "c2.presentation",
        "import wargame",
        "from wargame",
        "import tools",
        "from tools",
        "import ui",
        "from ui",
    ):
        assert forbidden not in runtime_src, (
            f"{module_name} 런타임 코드에 금지된 import 발견: {forbidden}"
        )


# ── (d) 인프라 harness_db 는 런타임에 application을 import하지 않음 ──────

def test_infra_harness_db_does_not_import_application_at_runtime():
    mod = importlib.import_module("c2.infrastructure.persistence.harness_db")
    src = inspect.getsource(mod)
    assert "TYPE_CHECKING" in src
    # 실행 시점(런타임) import 라인에 application 경로가 없어야 함 —
    # TYPE_CHECKING 블록 밖에서 "c2.application" 문자열이 나오면 안 됨.
    lines = src.splitlines()
    in_type_checking_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("if TYPE_CHECKING"):
            in_type_checking_block = True
            continue
        if in_type_checking_block:
            if line.startswith(" ") or line.startswith("\t"):
                continue  # TYPE_CHECKING 블록 내부 (허용됨)
            in_type_checking_block = False
        assert "c2.application" not in line, (
            f"harness_db.py 런타임 코드에 application import 발견: {line!r}"
        )


# ── (e) DI: 팩토리 통한 HarnessDB 획득 (기능 스모크) ──────────────────────

def test_direct_construction_without_factory_raises(monkeypatch):
    """의존성 역전 증명: 팩토리 미설정 → HarnessController(db 생략) 시 RuntimeError."""
    controller_mod = importlib.import_module("c2.application.harness.controller")
    monkeypatch.setattr(controller_mod, "_default_harness_db_factory", None)
    with pytest.raises(RuntimeError):
        controller_mod.HarnessController(engine_factory=lambda: None)


def test_di_factory_smoke_with_temp_db(tmp_path, monkeypatch):
    """팩토리를 임시 DB 경로로 주입 → 컨트롤러가 정상적으로 HarnessDB를 획득."""
    controller_mod = importlib.import_module("c2.application.harness.controller")
    from c2.infrastructure.persistence.harness_db import HarnessDB

    db_path = tmp_path / "test_harness.db"
    monkeypatch.setattr(
        controller_mod, "_default_harness_db_factory", lambda: HarnessDB(db_path)
    )

    ctrl = controller_mod.HarnessController(engine_factory=lambda: None)
    stats = ctrl.get_db_stats()
    assert "total_episodes" in stats
    assert db_path.exists()


def test_legacy_shim_wires_default_factory(tmp_path, monkeypatch):
    """wargame.harness import 시 기본 HarnessDB 팩토리가 주입됨을 확인."""
    import wargame.harness  # noqa: F401  (팩토리 wiring 발생)
    controller_mod = importlib.import_module("c2.application.harness.controller")

    assert controller_mod._default_harness_db_factory is not None

    # 실제 팩토리 호출은 기본 data/harness.db 경로를 사용하므로,
    # 팩토리가 HarnessDB 인스턴스를 만들어낼 수 있는지만 별도 임시 경로로 검증.
    from c2.infrastructure.persistence.harness_db import HarnessDB

    monkeypatch.setattr(
        controller_mod, "_default_harness_db_factory", lambda: HarnessDB(tmp_path / "legacy.db")
    )
    ctrl = wargame.harness.HarnessController(engine_factory=lambda: None)
    assert ctrl.get_db_stats() is not None
