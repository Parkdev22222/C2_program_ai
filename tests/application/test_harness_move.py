"""Task 26/33: 하네스(학습/평가) — c2.application.harness + c2.infrastructure.persistence.harness_db.

- 오케스트레이션(controller/episode_runner/metrics/rule_extractor/rule_manager/
  tactical_memory)은 c2.application.harness 에 있다.
- SQLite 영속 저장(HarnessDB)은 c2.infrastructure.persistence.harness_db 에 있다.
- 애플리케이션 하네스는 인프라(HarnessDB)를 런타임에 import 하지 않는다 —
  controller의 HarnessDB 의존은 DI 팩토리(Task 20의 EventStore 패턴과 동일)로 역전.
- 인프라 harness_db는 애플리케이션(EpisodeMetrics)을 런타임에 import 하지 않는다 —
  TYPE_CHECKING 가드만 사용 (타입힌트 전용).
- 기본 HarnessDB 팩토리는 tests/conftest.py 의 세션 autouse fixture 가 주입한다
  (Task 33: 레거시 shim import 소비 제거 이후 default factory wiring을 conftest 로 이관).
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


def test_default_harness_db_factory_wired_via_conftest():
    """tests/conftest.py 의 autouse fixture 가 기본 HarnessDB 팩토리를 주입함을 확인
    (Task 33: 레거시 wargame.harness shim import 없이도 db 미주입 생성이 동작해야 함)."""
    controller_mod = importlib.import_module("c2.application.harness.controller")

    assert controller_mod._default_harness_db_factory is not None

    ctrl = controller_mod.HarnessController(engine_factory=lambda: None)
    assert ctrl.get_db_stats() is not None
