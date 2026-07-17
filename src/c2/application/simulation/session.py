"""WargameSession — 워게임 세션의 엔진 생명주기를 소유하는 애플리케이션 객체.

과거 `ui/gradio_app.py`의 모듈 전역(`_wg_engine`, `_wg_planner`, `_wg_graph_store`,
`_wg_ontology_writer`)과 `_wg_ensure_engine`/`_wg_register_engine`/`_wg_ensure_ontology`
함수로 흩어져 있던 "엔진 생명주기" 책임을 세션 인스턴스로 이식한 것 (Task 29A).

`c2.application` 계층이므로 이 모듈은 `c2.domain`/`c2.application`/표준 라이브러리만
import한다. `tools`(presentation)·`ui`(presentation)·`c2.infrastructure`는 직접
import하지 않고, 아래 4가지를 생성자에서 주입받는다:

- `engine_factory()`      : `WargameEngine` 인스턴스를 생성 (기본값은 application 내부의
                             `c2.application.simulation.engine`/`scenario`만 사용).
- `tool_register_hook(engine)` : presentation 계층 8개 툴에 엔진을 등록하는 훅
                             (실체는 composition/gradio_app에서 주입; 미주입 시 no-op).
- `graph_store_factory()` : 온톨로지 그래프 스토어 생성(infra 구현; 미주입 시 온톨로지
                             적재 없이 동작).
- `ontology_writer_factory(engine, graph_store)` : `OntologyWriter` 유사 객체 생성
                             (미주입 시 기본 no-op writer가 사용된다).
- `agent`                 : 채팅/재계획에 쓰이는 에이전트 객체(이 태스크에서는 보관만).

CLAUDE.md 규칙: 콜백 4종(`on_new_opfor_detection`/`on_blufor_cp_threshold`/
`on_blufor_air_hit`/`on_target_moved`)은 `ensure_engine()`과 `reset()` 양쪽에서
항상 (재)등록되어야 한다.
"""

from __future__ import annotations

import logging
import queue
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class _NullOntologyWriter:
    """graph_store_factory/ontology_writer_factory 미주입 시 사용되는 no-op writer."""

    def __init__(self, engine=None, graph_store=None):
        self.engine = engine
        self.graph_store = graph_store

    def start(self):
        pass

    def request_flush(self):
        pass

    def reset(self, wipe: bool = False):
        pass


def _default_engine_factory():
    """application 계층 내부(엔진+시나리오)만으로 구성된 기본 엔진 팩토리.

    EventStore(DB) 주입은 `c2.application.simulation.engine`의
    `_default_event_store_factory` DI(이미 wargame.engine shim 등에서 배선됨)에
    위임한다 — 여기서 infra를 직접 import하지 않는다.
    """
    from c2.application.simulation.engine import WargameEngine
    from c2.application.simulation.scenario import setup_bn_vs_bn

    return WargameEngine(setup_bn_vs_bn())


class WargameSession:
    """워게임 세션의 엔진 생명주기(생성/콜백등록/리셋/시작정지/배속)를 소유한다."""

    def __init__(
        self,
        *,
        engine_factory: Optional[Callable[[], Any]] = None,
        tool_register_hook: Optional[Callable[[Any], None]] = None,
        graph_store_factory: Optional[Callable[[], Any]] = None,
        ontology_writer_factory: Optional[Callable[[Any, Any], Any]] = None,
        agent: Any = None,
    ):
        self._engine_factory = engine_factory or _default_engine_factory
        self._tool_register_hook = tool_register_hook
        self._graph_store_factory = graph_store_factory
        self._ontology_writer_factory = ontology_writer_factory or (
            lambda engine, graph_store: _NullOntologyWriter(engine, graph_store)
        )
        self.agent = agent

        self.engine: Optional[Any] = None
        self.planner: Optional[Any] = None
        self.graph_store: Optional[Any] = None
        self.ontology_writer: Optional[Any] = None

        # 세션이 소유하는 자동 재계획 이벤트 큐. 큐 이벤트 형식(CLAUDE.md):
        #   ("detection",    enemy_id, unit_type, x, y)
        #   ("cp_threshold", unit_id, unit_type, threshold_pct, current_cp)
        #   ("air_hit",      unit_id, unit_type, call_sign, current_cp)
        #   ("target_moved", unit_id, unit_type, target_id, moved_dist_m)
        # Task 29B(replan 워커)가 이 큐를 소비한다.
        self.detection_queue: "queue.Queue" = queue.Queue()

    # ── 콜백 enqueue (엔진 틱 스레드에서 호출됨 — 큐에만 넣고 즉시 반환) ──

    def _detection_enqueue(self, enemy_id: str, unit_type: str, x: float, y: float) -> None:
        self.detection_queue.put_nowait(("detection", enemy_id, unit_type, x, y))
        self._ontology_flush()

    def _cp_threshold_enqueue(
        self, unit_id: str, unit_type: str, threshold_pct: float, current_cp: float
    ) -> None:
        self.detection_queue.put_nowait(
            ("cp_threshold", unit_id, unit_type, threshold_pct, current_cp)
        )
        self._ontology_flush()

    def _air_hit_enqueue(
        self, unit_id: str, unit_type: str, call_sign: str, current_cp: float
    ) -> None:
        self.detection_queue.put_nowait(("air_hit", unit_id, unit_type, call_sign, current_cp))
        self._ontology_flush()

    def _target_moved_enqueue(
        self, unit_id: str, unit_type: str, target_id: str, moved_dist: float
    ) -> None:
        self.detection_queue.put_nowait(
            ("target_moved", unit_id, unit_type, target_id, moved_dist)
        )

    def _ontology_flush(self) -> None:
        """온톨로지 적재기에 비동기 즉시 스냅샷 요청 (엔진 틱 스레드 논블로킹)."""
        if self.ontology_writer is not None:
            try:
                self.ontology_writer.request_flush()
            except Exception:
                pass

    def _register_callbacks(self, engine) -> None:
        """콜백 4종을 항상 (재)등록한다 (CLAUDE.md 필수)."""
        engine.on_new_opfor_detection = self._detection_enqueue
        engine.on_blufor_cp_threshold = self._cp_threshold_enqueue
        engine.on_blufor_air_hit = self._air_hit_enqueue
        engine.on_target_moved = self._target_moved_enqueue

    # ── 엔진 생명주기 ──────────────────────────────────────────────

    def register_engine(self, engine) -> None:
        """presentation 계층 툴들에 엔진을 등록한다 (주입된 훅 경유)."""
        if self._tool_register_hook is not None:
            try:
                self._tool_register_hook(engine)
            except Exception:
                logger.exception("tool_register_hook 실행 중 오류")

    def ensure_ontology(self, engine) -> None:
        """온톨로지 그래프 스토어 + 실시간 적재기를 준비한다 (세션당 1회 생성)."""
        try:
            if self.graph_store is None and self._graph_store_factory is not None:
                self.graph_store = self._graph_store_factory()
            if self.ontology_writer is None:
                self.ontology_writer = self._ontology_writer_factory(engine, self.graph_store)
                self.ontology_writer.start()
            else:
                self.ontology_writer.engine = engine
        except Exception as e:
            logger.warning(f"온톨로지 적재기 초기화 실패 (무시): {e}")

    def ensure_engine(self):
        """엔진이 없으면 생성하고, 콜백 4종 + 툴등록 + 온톨로지를 준비한 뒤 반환한다."""
        if self.engine is None:
            engine = self._engine_factory()
            self.engine = engine
            self.register_engine(engine)
            self._register_callbacks(engine)
            # UAV 완전 정찰 가정 (철원 시나리오) — 존재하는 속성일 때만 설정
            try:
                engine.full_recon = True
            except Exception:
                pass
            self.ensure_ontology(engine)
        return self.engine

    def reset(self, units=None) -> dict:
        """엔진 리셋 — 콜백 4종을 반드시 재등록한다 (CLAUDE.md).

        `units`가 주어지지 않으면 engine_factory와 동일한 기본 시나리오
        (`setup_bn_vs_bn`)로 새로 배치한다.
        """
        if units is None:
            from c2.application.simulation.scenario import setup_bn_vs_bn

            units = setup_bn_vs_bn()

        if self.engine is None:
            self.ensure_engine()
        engine = self.engine
        engine.reset(units)

        self.register_engine(engine)
        self._register_callbacks(engine)
        try:
            engine.full_recon = True
        except Exception:
            pass

        self.ensure_ontology(engine)
        if self.ontology_writer is not None:
            try:
                self.ontology_writer.reset(wipe=True)
            except Exception:
                pass

        return self.get_state()

    def start_pause(self) -> dict:
        """실행/일시정지 토글 — {"running": bool, "label": str} 반환."""
        engine = self.ensure_engine()
        if engine.running:
            engine.stop()
            label = "▶ 시뮬레이션 시작"
        else:
            engine.start()
            label = "⏸ 일시정지"
        return {"running": engine.running, "label": label}

    def set_timescale(self, scale: float) -> None:
        engine = self.ensure_engine()
        engine.time_scale = float(scale)

    def stop(self) -> None:
        if self.engine is not None:
            self.engine.stop()

    def get_state(self) -> dict:
        engine = self.ensure_engine()
        return engine.get_state()
