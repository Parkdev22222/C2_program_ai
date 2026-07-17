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
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class _NullReplanHooks:
    """presentation 계층 툴 연동(replan_hooks) 미주입 시 사용되는 no-op 훅.

    자동 재계획 워커(`c2.application.simulation.replan`)가 presentation 툴
    (`tools.wargame_mission_tool`의 apply tracker 등, `agent.battlefield_agent`의
    학습규칙 조회)과 협력하기 위한 경계. application은 tools/agent를 import할 수
    없으므로 composition/gradio_app에서 실체를 주입한다. 미주입 시 이 no-op이
    쓰이며, 이는 과거 gradio 워커의 try/except 폴백(빈 문자열/False/빈 dict)과
    동일하게 동작한다.
    """

    def reset_apply_tracker(self) -> None:
        pass

    def was_plan_applied_since(self, ts: float) -> bool:
        return False

    def get_last_applied_plan(self) -> dict:
        return {}

    def set_resume_on_apply(self, value: bool) -> None:
        pass

    def get_instruction_section(self, name: str) -> str:
        return ""

    def assess_recon_need(self) -> dict:
        return {}

    def recommend_recon_routes(self) -> dict:
        return {}

    def append_learned_rule(self, rule: str) -> None:
        pass


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
    from c2.application.simulation.scenario import setup_cheorwon_bn

    return WargameEngine(setup_cheorwon_bn())


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
        replan_hooks: Any = None,
    ):
        self._engine_factory = engine_factory or _default_engine_factory
        self._tool_register_hook = tool_register_hook
        self._graph_store_factory = graph_store_factory
        self._ontology_writer_factory = ontology_writer_factory or (
            lambda engine, graph_store: _NullOntologyWriter(engine, graph_store)
        )
        self.agent = agent
        # presentation 툴 연동 훅 (자동 재계획 워커가 사용). 미주입 시 no-op.
        self.replan_hooks: Any = replan_hooks or _NullReplanHooks()

        self.engine: Optional[Any] = None
        self.planner: Optional[Any] = None
        self.graph_store: Optional[Any] = None
        self.ontology_writer: Optional[Any] = None
        self.harness_controller: Optional[Any] = None

        # 세션이 소유하는 자동 재계획 이벤트 큐. 큐 이벤트 형식(CLAUDE.md):
        #   ("detection",    enemy_id, unit_type, x, y)
        #   ("cp_threshold", unit_id, unit_type, threshold_pct, current_cp)
        #   ("air_hit",      unit_id, unit_type, call_sign, current_cp)
        #   ("target_moved", unit_id, unit_type, target_id, moved_dist_m)
        # Task 29B(replan 워커)가 이 큐를 소비한다.
        self.detection_queue: "queue.Queue" = queue.Queue()

        # 자동 재계획 상태/동기화 (replan 워커가 참조).
        self._auto_plan_lock = threading.Lock()   # 동시 자동 계획 방지
        self.auto_plan_status: dict = {"active": False, "message": "", "started_at": 0.0}
        self.last_replan_tick: int = -30           # 30틱 쿨다운 기준
        self._worker_stop = threading.Event()      # 탐지 워커 정지 신호
        self._detection_thread: Optional[threading.Thread] = None

    # ── 콜백 enqueue (엔진 틱 스레드에서 호출됨 — 큐에만 넣고 즉시 반환) ──

    def enqueue_detection(self, enemy_id: str, unit_type: str, x: float, y: float) -> None:
        self.detection_queue.put_nowait(("detection", enemy_id, unit_type, x, y))
        self._ontology_flush()

    def enqueue_cp_threshold(
        self, unit_id: str, unit_type: str, threshold_pct: float, current_cp: float
    ) -> None:
        self.detection_queue.put_nowait(
            ("cp_threshold", unit_id, unit_type, threshold_pct, current_cp)
        )
        self._ontology_flush()

    def enqueue_air_hit(
        self, unit_id: str, unit_type: str, call_sign: str, current_cp: float
    ) -> None:
        self.detection_queue.put_nowait(("air_hit", unit_id, unit_type, call_sign, current_cp))
        self._ontology_flush()

    def enqueue_target_moved(
        self, unit_id: str, unit_type: str, target_id: str, moved_dist: float
    ) -> None:
        self.detection_queue.put_nowait(
            ("target_moved", unit_id, unit_type, target_id, moved_dist)
        )

    # 하위호환 별칭 (기존 private 이름 참조 대비)
    _detection_enqueue = enqueue_detection
    _cp_threshold_enqueue = enqueue_cp_threshold
    _air_hit_enqueue = enqueue_air_hit
    _target_moved_enqueue = enqueue_target_moved

    # ── 탐지 워커 수명주기 ─────────────────────────────────────────

    def start_detection_worker(self) -> None:
        """세션 탐지 큐를 소비하는 백그라운드 워커 스레드를 시작한다 (1회)."""
        if self._detection_thread is not None and self._detection_thread.is_alive():
            return
        from c2.application.simulation.replan import detection_worker

        self._worker_stop.clear()
        t = threading.Thread(
            target=detection_worker, args=(self,), daemon=True, name="DetectionWorker"
        )
        self._detection_thread = t
        t.start()

    def stop_detection_worker(self, timeout: float = 5.0) -> None:
        """탐지 워커 스레드에 정지 신호를 보내고 종료를 대기한다."""
        self._worker_stop.set()
        t = self._detection_thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._detection_thread = None

    def _ontology_flush(self) -> None:
        """온톨로지 적재기에 비동기 즉시 스냅샷 요청 (엔진 틱 스레드 논블로킹)."""
        if self.ontology_writer is not None:
            try:
                self.ontology_writer.request_flush()
            except Exception:
                pass

    def _register_callbacks(self, engine) -> None:
        """콜백 4종을 항상 (재)등록한다 (CLAUDE.md 필수)."""
        engine.on_new_opfor_detection = self.enqueue_detection
        engine.on_blufor_cp_threshold = self.enqueue_cp_threshold
        engine.on_blufor_air_hit = self.enqueue_air_hit
        engine.on_target_moved = self.enqueue_target_moved

    # ── 엔진 생명주기 ──────────────────────────────────────────────

    def _ensure_planner(self) -> None:
        """자동 재계획 폴백/파싱에 쓰이는 MissionPlanner를 준비한다 (세션당 1회)."""
        if self.planner is None:
            from c2.application.agent.mission_planner import MissionPlanner

            self.planner = MissionPlanner()

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
            self._ensure_planner()
            self.register_engine(engine)
            self._register_callbacks(engine)
            # UAV 완전 정찰 가정 (철원 시나리오) — 존재하는 속성일 때만 설정
            try:
                engine.full_recon = True
                # full_recon은 원래 _tick() 중에만 인텔에 반영되므로, 첫 틱 전(시작/일시정지)
                # 상태에서는 OPFOR가 approximate로 남아 채팅("탐지 없음")과 공격계획("모두 탐지")
                # 표시가 어긋난다. 초기 인텔을 즉시 갱신해 두 경로를 일치시킨다.
                # (_update_intelligence는 유닛 이동·전투를 advance하지 않고 인텔만 갱신)
                engine._update_intelligence()
            except Exception:
                pass
            self.ensure_ontology(engine)
        return self.engine

    def reset(self, units=None) -> dict:
        """엔진 리셋 — 콜백 4종을 반드시 재등록한다 (CLAUDE.md).

        `units`가 주어지지 않으면 engine_factory와 동일한 기본 시나리오
        (`setup_cheorwon_bn`)로 새로 배치한다.
        """
        if units is None:
            from c2.application.simulation.scenario import setup_cheorwon_bn

            units = setup_cheorwon_bn()

        if self.engine is None:
            self.ensure_engine()
        engine = self.engine
        engine.reset(units)

        self._ensure_planner()
        # 엔진 틱이 0으로 초기화되므로 재계획 쿨다운 기준도 초기화
        self.last_replan_tick = -30
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

    # ── 세션 ops (Task 29C) — 데이터(dict) 반환, figure 생성은 presentation 몫 ──

    def apply_custom_scenario(self, scenario_config: dict) -> dict:
        """사용자 정의 시나리오 적용 — 부대 구성·배치 변경 후 엔진 리셋.

        과거 `ui/gradio_app.py`의 `wargame_apply_custom_scenario()`(전역 `_wg_engine`/
        `_wg_planner` 조작)를 세션 메서드로 이식한 것. 직접 `WargameEngine(units)`를
        생성하던 부분은 주입된 `engine_factory` 경로(`ensure_engine()`)를 거치도록
        치환했다(엔진 생성 즉시 콜백4종/툴등록/온톨로지가 갖춰지도록).
        """
        try:
            from c2.application.planning.mission_session import update_valid_company_ids
            from c2.application.simulation.scenario import setup_custom_scenario

            blufor_defs = scenario_config.get("blufor", [])
            opfor_defs = scenario_config.get("opfor", [])

            if not blufor_defs:
                return {"ok": False, "error": "BLUFOR 부대가 없습니다."}
            if not opfor_defs:
                return {"ok": False, "error": "OPFOR 부대가 없습니다."}

            update_valid_company_ids({bd["id"] for bd in blufor_defs})

            units = setup_custom_scenario(blufor_defs, opfor_defs)

            if self.engine is not None:
                self.engine.reset(units)
            else:
                self.ensure_engine()
                self.engine.reset(units)

            self._ensure_planner()
            # 콜백 4종 재등록 (CLAUDE.md) — 세션 enqueue → 세션 큐 → 세션 워커
            self._register_callbacks(self.engine)

            logger.info(
                "사용자 정의 시나리오 적용 완료: BLUFOR %d개, OPFOR %d개",
                len(blufor_defs), len(opfor_defs),
            )
            return {"ok": True, "blufor": len(blufor_defs), "opfor": len(opfor_defs)}
        except Exception as e:
            logger.exception("apply_custom_scenario 오류")
            return {"ok": False, "error": str(e)}

    def request_recon_plan(self, history: Optional[list] = None) -> dict:
        """정찰 임무계획 수립 — 오케스트레이션은 `replan.request_recon_plan`에 위임."""
        from c2.application.simulation.replan import request_recon_plan as _impl

        return _impl(self, history)

    def request_attack_plan(self, history: Optional[list] = None) -> dict:
        """공격 임무계획 수립 — 오케스트레이션은 `replan.request_attack_plan`에 위임."""
        from c2.application.simulation.replan import request_attack_plan as _impl

        return _impl(self, history)

    def chat_send(self, message: str, history: Optional[list] = None) -> dict:
        """전술채팅 메시지 처리 — 오케스트레이션은 `replan.chat_send`에 위임."""
        from c2.application.simulation.replan import chat_send as _impl

        return _impl(self, message, history)

    def evaluate_and_learn(self, history: Optional[list] = None) -> dict:
        """전투 결과 평가 + 전술 규칙 학습 — 오케스트레이션은 `replan.evaluate_and_learn`에 위임."""
        from c2.application.simulation.replan import evaluate_and_learn as _impl

        return _impl(self, history)

    # ── 하니스(학습/평가) 세션 ops (Task 29D) — 데이터(dict) 반환 ──────────
    #
    # `HarnessController`(`c2.application.harness.controller`)는 이미 application
    # 계층이므로(Task 26) 세션이 직접 구성/보유한다(application → application, 허용).
    # HarnessDB(인프라)는 컨트롤러 내부의 DI 팩토리(`set_default_harness_db_factory`,
    # 레거시 `wargame.harness` shim이 기본 wiring)에 위임되며 세션은 이를 import하지
    # 않는다. 마크다운/Gradio 튜플 조립은 gradio 래퍼가 담당한다.

    def init_harness_controller(self):
        """하네스 컨트롤러를 초기화한다 (세션당 1회, 실패 시 None)."""
        if self.harness_controller is not None:
            return self.harness_controller
        try:
            from c2.application.harness.controller import HarnessController

            def _harness_engine_factory():
                engine = self._engine_factory()
                try:
                    engine.full_recon = True  # 철원 시나리오: UAV 완전정찰
                except Exception:
                    pass
                self.register_engine(engine)
                return engine

            self._ensure_planner()
            self.harness_controller = HarnessController(
                engine_factory=_harness_engine_factory,
                agent=self.agent,
                planner=self.planner,
            )
            logger.info("HarnessController initialized")
        except Exception as e:
            logger.warning(f"Failed to init HarnessController: {e}")
            self.harness_controller = None
        return self.harness_controller

    def harness_start_training(self, n_episodes: int, replan_interval: int) -> dict:
        """하네스 학습을 시작한다 — {"ok", "reason", "chat_entry", "status_message"} 반환."""
        ctrl = self.harness_controller or self.init_harness_controller()
        if ctrl is None:
            return {
                "ok": False,
                "reason": "init_failed",
                "chat_entry": ("🔬 하네스 학습", "HarnessController 초기화 실패"),
                "status_message": "초기화 실패",
            }

        if ctrl._running:
            return {
                "ok": False,
                "reason": "already_running",
                "chat_entry": None,
                "status_message": "이미 실행 중",
            }

        def _progress_cb(current, total, metrics):
            pass  # 폴링 방식으로 UI 업데이트

        ctrl.start_training(
            n_episodes=int(n_episodes),
            replan_interval_ticks=int(replan_interval),
            on_progress=_progress_cb,
        )
        return {
            "ok": True,
            "reason": "started",
            "chat_entry": ("🔬 하네스 학습 시작", f"{n_episodes}개 에피소드 학습 시작..."),
            "status_message": f"학습 시작: {n_episodes}개 에피소드",
        }

    def harness_status(self) -> dict:
        """하네스 학습 진행 상황 — {"initialized", "progress", "stats"} 반환."""
        ctrl = self.harness_controller
        if ctrl is None:
            return {"initialized": False}
        return {
            "initialized": True,
            "progress": ctrl.get_progress(),
            "stats": ctrl.get_db_stats(),
        }

    def harness_stop_training(self) -> dict:
        """실행 중인 하네스 학습을 중지한다 — {"stopped", "chat_entry"} 반환."""
        ctrl = self.harness_controller
        if ctrl is not None and ctrl._running:
            ctrl.stop_training()
            return {"stopped": True, "chat_entry": ("🔬 하네스", "학습 중지 요청")}
        return {"stopped": False, "chat_entry": None}

    def harness_rules(self) -> dict:
        """현재 활성 규칙 데이터를 반환한다 (마크다운 조립은 gradio 래퍼 담당).

        컨트롤러 미초기화 시 `replan_hooks.get_instruction_section()`(presentation
        agent 지시사항 폴백)으로 원문(raw) 텍스트를 반환한다.
        """
        ctrl = self.harness_controller
        if ctrl is None:
            recon_text = self.replan_hooks.get_instruction_section("RECON")
            attack_text = self.replan_hooks.get_instruction_section("ATTACK")
            learned_text = self.replan_hooks.get_instruction_section("LEARNED_RULES")
            if not recon_text and not attack_text and not learned_text:
                return {"initialized": False}
            result: dict = {
                "initialized": False,
                "recon_text": recon_text,
                "attack_text": attack_text,
                "learned_text": learned_text,
            }
        else:
            rules = ctrl.get_active_rules()
            result = {
                "initialized": True,
                "recon_rules": rules.get("RECON", []),
                "attack_rules": rules.get("ATTACK", []),
                "learned_rules": rules.get("LEARNED_RULES", []),
            }

        try:
            from c2.application.harness.tactical_memory import get_tactical_memory

            result["penalty_zones"] = get_tactical_memory().get_penalty_zones()
        except Exception:
            result["penalty_zones"] = []

        return result
