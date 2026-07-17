"""조립 루트(composition root) — `WargameSession`의 전체 의존성을 한곳에서 wiring한다.

`c2.composition`은 domain/application/infrastructure/presentation 전 계층을 자유롭게
import할 수 있는 유일한 패키지다(`.importlinter`의 `layers`/`forbidden` 계약에 포함되지
않음 — `PYTHONPATH=src lint-imports` 로 3 kept 0 broken 유지 확인).

`build_session(agent=None) -> WargameSession` 이 wiring하는 항목(Task 32 체크리스트):

1. **EventStore 기본 팩토리** — `set_default_event_store_factory(lambda: WargameDB())`.
   `WargameSession`의 기본 `engine_factory`가 생성하는 `WargameEngine`은 DB 미주입 시
   이 전역 팩토리를 사용한다(`c2.application.simulation.engine._default_event_store_factory`).
   `build_session()` 안에서 세션 생성보다 먼저 호출되므로, 엔진이 실제로 생성되는 시점
   (`ensure_engine()`)에는 항상 팩토리가 준비되어 있다 — 29A RuntimeError 방지.
2. **tool_register_hook** — presentation 8개 툴(`register_wargame_engine`)에 엔진을 등록.
3. **graph_store_factory** — `build_graph_store()` + 온톨로지 조회 툴에 스토어 등록.
4. **ontology_writer_factory** — `OntologyWriter` 생성(gradio `_wg_ontology_writer_factory` 반영).
5. **replan_hooks** — 자동 재계획 워커(`c2.application.simulation.replan`)가 쓰는
   presentation 훅 8종(`_ContainerReplanHooks`, gradio `_GradioReplanHooks`와 동일 계약).
6. **planning advisors** — `set_planning_advisors(recon=, attack=, fire=)`
   (레거시 `wargame/llm_planner.py` shim이 하던 배선을 이관).
7. **HarnessStore 기본 팩토리** — `set_default_harness_db_factory(lambda: HarnessDB())`.
8. **agent** — 주입된 agent를 `WargameSession(agent=...)` 생성자에 전달.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── 1. EventStore 기본 팩토리 ────────────────────────────────────────────────

def _wire_event_store_default() -> None:
    from c2.application.simulation.engine import set_default_event_store_factory
    from c2.infrastructure.persistence.sqlite_event_store import WargameDB

    set_default_event_store_factory(lambda: WargameDB())


# ── 7. HarnessStore 기본 팩토리 ──────────────────────────────────────────────

def _wire_harness_db_default() -> None:
    from c2.application.harness.controller import set_default_harness_db_factory
    from c2.infrastructure.persistence.harness_db import HarnessDB

    set_default_harness_db_factory(lambda: HarnessDB())


# ── 6. 계획 자문(advisor) 등록 ───────────────────────────────────────────────

def _wire_planning_advisors() -> None:
    from c2.application.agent.mission_planner import set_planning_advisors
    from c2.presentation.tools.wargame_attack_advisor_tool import (
        get_optimal_attack_positions,
    )
    from c2.presentation.tools.wargame_fire_priority_tool import (
        get_fire_priority_schedule,
    )
    from c2.presentation.tools.wargame_recon_tool import recommend_recon_routes

    set_planning_advisors(
        recon=recommend_recon_routes,
        attack=get_optimal_attack_positions,
        fire=get_fire_priority_schedule,
    )


# ── 2. presentation 8개 툴에 엔진 등록 ───────────────────────────────────────

_TOOL_MODULES = [
    "c2.presentation.tools.wargame_query_tool",
    "c2.presentation.tools.wargame_mission_tool",
    "c2.presentation.tools.wargame_strategy_tool",
    "c2.presentation.tools.wargame_attack_advisor_tool",
    "c2.presentation.tools.wargame_fire_priority_tool",
    "c2.presentation.tools.wargame_recon_tool",
    "c2.presentation.tools.wargame_opfor_routes_tool",
    "c2.presentation.tools.coa_analysis_tool",
]


def _tool_register_hook(engine: Any) -> None:
    """`WargameSession.register_engine()`이 호출하는 훅 — 8개 툴에 엔진 등록."""
    for mod_name in _TOOL_MODULES:
        try:
            mod = importlib.import_module(mod_name)
            mod.register_wargame_engine(engine)
        except Exception:
            logger.exception("register_wargame_engine 실패: %s", mod_name)


# ── 3. 온톨로지 그래프 스토어 팩토리 (+ 조회 툴 등록) ────────────────────────

def _graph_store_factory() -> Any:
    from c2.application.ontology.wargame_builder import WARGAME_SCENARIO_ID
    from c2.infrastructure.ontology.factory import build_graph_store
    from c2.presentation.tools.ontology_query_tool import register_graph_store

    gs = build_graph_store()
    register_graph_store(gs, WARGAME_SCENARIO_ID)
    return gs


# ── 4. OntologyWriter 팩토리 ─────────────────────────────────────────────────

def _ontology_writer_factory(engine: Any, graph_store: Any) -> Any:
    from c2.application.ontology.writer import OntologyWriter

    return OntologyWriter(engine, graph_store)


# ── 5. replan_hooks (자동 재계획 워커 ↔ presentation 툴 연동) ───────────────

class _ContainerReplanHooks:
    """`c2.application.simulation.replan`이 사용하는 presentation 연동 훅.

    gradio_app의 `_GradioReplanHooks`와 동일한 계약(메서드 8종)이며, 레거시
    top-level shim(`tools.*`/`agent.battlefield_agent`) 대신 신규 `c2.presentation.*`
    경로에서 직접 import한다.
    """

    def reset_apply_tracker(self) -> None:
        try:
            from c2.presentation.tools.wargame_mission_tool import (
                reset_apply_tracker,
            )

            reset_apply_tracker()
        except Exception:
            pass

    def was_plan_applied_since(self, ts: float) -> bool:
        try:
            from c2.presentation.tools.wargame_mission_tool import (
                was_plan_applied_since,
            )

            return was_plan_applied_since(ts)
        except Exception:
            return False

    def get_last_applied_plan(self) -> dict:
        try:
            from c2.presentation.tools.wargame_mission_tool import (
                get_last_applied_plan,
            )

            return get_last_applied_plan()
        except Exception:
            return {}

    def set_resume_on_apply(self, value: bool) -> None:
        try:
            from c2.presentation.tools.wargame_mission_tool import (
                set_resume_on_apply,
            )

            set_resume_on_apply(value)
        except Exception:
            pass

    def get_instruction_section(self, name: str) -> str:
        try:
            from c2.presentation.agent.battlefield_agent import (
                get_instruction_section,
            )

            return get_instruction_section(name)
        except Exception:
            return ""

    def assess_recon_need(self) -> dict:
        from c2.presentation.tools.wargame_recon_tool import assess_recon_need

        return assess_recon_need()

    def recommend_recon_routes(self) -> dict:
        from c2.presentation.tools.wargame_recon_tool import recommend_recon_routes

        return recommend_recon_routes()

    def append_learned_rule(self, rule: str) -> None:
        from c2.presentation.agent.battlefield_agent import append_learned_rule

        append_learned_rule(rule)


# ── 조립 루트 진입점 ──────────────────────────────────────────────────────

def build_session(agent: Optional[Any] = None):
    """완전히 wiring된 `WargameSession`을 반환한다.

    호출 순서가 중요하다: 엔진이 실제로 생성되기 전에 EventStore/HarnessStore
    기본 팩토리와 계획 자문(advisor)을 먼저 전역 등록한 뒤, 그 팩토리들에 의존하는
    `WargameSession`을 구성한다(29A RuntimeError 풋건 방지).
    """
    _wire_event_store_default()
    _wire_harness_db_default()
    _wire_planning_advisors()

    from c2.application.simulation.session import WargameSession

    return WargameSession(
        tool_register_hook=_tool_register_hook,
        graph_store_factory=_graph_store_factory,
        ontology_writer_factory=_ontology_writer_factory,
        agent=agent,
        replan_hooks=_ContainerReplanHooks(),
    )
