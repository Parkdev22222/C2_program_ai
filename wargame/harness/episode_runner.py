"""
에피소드 실행 모듈.

WargameEngine을 생성/초기화하고 한 에피소드를 실행하며
EpisodeMetrics를 수집하여 반환합니다.
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .metrics import EpisodeMetrics, collect_metrics

logger = logging.getLogger(__name__)


class EpisodeRunner:
    """
    단일 워게임 에피소드를 실행하고 메트릭을 수집합니다.

    engine_factory를 통해 새 엔진을 생성하며,
    BattlefieldAgent / MissionPlanner와 연동하여 임무계획을 적용합니다.
    """

    def __init__(
        self,
        engine_factory: Callable,
        agent=None,
        planner=None,
    ):
        """
        EpisodeRunner 초기화.

        Args:
            engine_factory: callable() -> WargameEngine 새 엔진 생성 팩토리
            agent: BattlefieldAgent 인스턴스 (없으면 규칙 기반 폴백)
            planner: MissionPlanner 인스턴스 (없으면 간단한 규칙 적용)
        """
        self._engine_factory = engine_factory
        self._agent = agent
        self._planner = planner

    def run_episode(
        self,
        max_real_seconds: float = 90.0,
        replan_interval_ticks: int = 120,
        initial_mission: str = "recon",  # "recon" | "attack" | "auto"
    ) -> EpisodeMetrics:
        """
        한 에피소드를 실행하고 메트릭을 반환합니다.

        Args:
            max_real_seconds: 에피소드 최대 실행 시간 (실제 초)
            replan_interval_ticks: 재계획 주기 (틱 단위)
            initial_mission: 초기 임무 유형 ("recon" | "attack" | "auto")

        Returns:
            EpisodeMetrics 인스턴스
        """
        engine = None
        last_plan: dict = {}
        last_plan_tick: int = 0

        try:
            # 1. 새 엔진 생성
            engine = self._engine_factory()
            logger.info("에피소드 시작: 새 엔진 생성")

            # 2. 엔진 시작
            engine.start()

            # 3. 초기 임무계획 적용
            try:
                initial_result = self._apply_initial_plan(engine, initial_mission)
                if initial_result:
                    last_plan = initial_result
                    last_plan_tick = 0
            except Exception as e:
                logger.warning(f"초기 임무계획 적용 실패: {e}")

            # 4. 메인 루프
            start_time = time.time()

            while True:
                # 시간 초과 확인
                if time.time() - start_time > max_real_seconds:
                    logger.info(f"에피소드 시간 초과: {max_real_seconds}초")
                    break

                # 상태 조회
                try:
                    state = engine.get_state()
                except Exception as e:
                    logger.error(f"get_state() 실패: {e}")
                    break

                # 승자 확인
                if state.get("winner"):
                    logger.info(f"에피소드 종료: 승자={state['winner']}")
                    break

                # 엔진 정지 확인
                if not state.get("running", True):
                    logger.info("에피소드 종료: 엔진 정지")
                    break

                # 재계획 여부 확인
                current_tick = state.get("tick", 0)
                if self._should_replan(engine, last_plan_tick, current_tick, replan_interval_ticks):
                    try:
                        new_plan = self._replan(engine, state)
                        if new_plan:
                            last_plan = new_plan
                            last_plan_tick = current_tick
                            logger.debug(f"재계획 적용: tick={current_tick}")
                    except Exception as e:
                        logger.warning(f"재계획 실패: {e}")

                time.sleep(1.0)

        except Exception as e:
            logger.error(f"run_episode 오류: {e}")
        finally:
            # 5. 엔진 정지
            if engine is not None:
                try:
                    engine.stop()
                except Exception as e:
                    logger.warning(f"engine.stop() 오류: {e}")

        # 6. 메트릭 수집
        if engine is not None:
            try:
                metrics = collect_metrics(engine, last_plan=last_plan)
                logger.info(f"에피소드 완료: {metrics.summary_str()}")
                return metrics
            except Exception as e:
                logger.error(f"메트릭 수집 실패: {e}")

        # 폴백: 기본 메트릭 반환
        return _default_metrics()

    def _apply_initial_plan(self, engine, mission_type: str) -> Optional[dict]:
        """
        초기 임무계획을 엔진에 적용합니다.

        Args:
            engine: WargameEngine 인스턴스
            mission_type: "recon" | "attack" | "auto"

        Returns:
            적용된 임무계획 딕셔너리 또는 None
        """
        if mission_type == "auto":
            # 정찰 필요 여부 자동 판단
            try:
                from tools.wargame_recon_tool import assess_recon_need
                # 일시적으로 엔진 등록
                import tools.wargame_recon_tool as recon_module
                _prev_engine = recon_module._wargame_engine
                recon_module._wargame_engine = engine

                recon_result = assess_recon_need()
                recon_module._wargame_engine = _prev_engine

                if recon_result.get("recon_needed", False):
                    mission_type = "recon"
                else:
                    mission_type = "attack"
            except Exception as e:
                logger.warning(f"auto 판단 실패, attack으로 폴백: {e}")
                mission_type = "attack"

        if mission_type == "recon":
            return self._apply_recon_plan(engine)
        elif mission_type == "attack":
            return self._apply_attack_plan(engine)

        return None

    def _apply_recon_plan(self, engine) -> Optional[dict]:
        """정찰 임무계획 적용."""
        try:
            from tools.wargame_recon_tool import recommend_recon_routes
            import tools.wargame_recon_tool as recon_module

            _prev = recon_module._wargame_engine
            recon_module._wargame_engine = engine

            result = recommend_recon_routes()
            recon_module._wargame_engine = _prev

            if result.get("status") == "success":
                plan = {"mission_plans": result.get("mission_plans", [])}
                # apply_json의 mission_plans만 엔진에 전달
                apply_payload = json.loads(result.get("apply_json", "{}"))
                if apply_payload.get("mission_plans"):
                    engine.apply_mission_plan(apply_payload)
                    logger.info("정찰 임무계획 적용 완료")
                    return plan
        except Exception as e:
            logger.warning(f"정찰 임무계획 적용 실패: {e}")

        return None

    def _apply_attack_plan(self, engine) -> Optional[dict]:
        """공격 임무계획 적용 (규칙 기반)."""
        try:
            if self._planner is not None:
                state = engine.get_state()
                plan = self._planner._rule_based(state)
            else:
                state = engine.get_state()
                plan = _simple_attack_plan(state)

            if plan and plan.get("mission_plans"):
                engine.apply_mission_plan(plan)
                logger.info(f"공격 임무계획 적용: {len(plan['mission_plans'])}개 부대")
                return plan
        except Exception as e:
            logger.warning(f"공격 임무계획 적용 실패: {e}")

        return None

    def _should_replan(
        self,
        engine,
        last_plan_tick: int,
        current_tick: int,
        interval: int,
    ) -> bool:
        """
        재계획 필요 여부를 판단합니다.

        재계획 조건:
        - 새로운 OPFOR 탐지
        - 주요 피해 발생
        - interval 틱 경과

        Args:
            engine: WargameEngine 인스턴스
            last_plan_tick: 마지막 계획 적용 틱
            current_tick: 현재 틱
            interval: 재계획 주기 틱

        Returns:
            True이면 재계획 필요
        """
        # interval 경과 확인
        if (current_tick - last_plan_tick) >= interval:
            return True

        # 새 탐지 또는 주요 피해 이벤트 확인
        try:
            recent_events = engine.db.get_recent_events(10)
            for ev in recent_events:
                ev_tick = ev.get("tick", 0)
                if ev_tick <= last_plan_tick:
                    continue
                etype = ev.get("event_type", "")
                if etype == "DETECTION":
                    return True
                if etype == "DESTROYED":
                    msg = ev.get("message", "")
                    # BLUFOR 부대 전투불능 시 재계획
                    if any(uid in msg for uid in ("Alpha", "Bravo", "Charlie", "Delta", "Echo")):
                        return True
        except Exception:
            pass

        return False

    def _replan(self, engine, state: dict) -> Optional[dict]:
        """재계획 실행."""
        if self._planner is not None:
            return self._planner._rule_based(state)
        return _simple_attack_plan(state)


def _simple_attack_plan(state: dict) -> dict:
    """간단한 규칙 기반 공격 임무계획 생성."""
    opfor_alive = [
        u for u in state.get("units", [])
        if u.get("side") == "OPFOR" and u.get("status") != "destroyed"
    ]
    if not opfor_alive:
        return {"reasoning": "모든 적 전멸.", "mission_plans": []}

    op_cx = sum(u["x"] for u in opfor_alive) / len(opfor_alive)
    op_cy = sum(u["y"] for u in opfor_alive) / len(opfor_alive)

    blufor = [
        u for u in state.get("units", [])
        if u.get("side") == "BLUFOR" and u.get("status") != "destroyed"
        and u.get("combat_power", 0) > 5
    ]

    plans = []
    for i, u in enumerate(blufor):
        cp = u.get("combat_power", 100)
        if cp < 30:
            plans.append({
                "company_id": u["id"],
                "mission_type": "defend",
                "waypoints": [[u["x"], u["y"]]],
                "objective": "현위치 방어",
            })
        else:
            offset = 500 if i % 2 == 0 else -500
            plans.append({
                "company_id": u["id"],
                "mission_type": "attack",
                "waypoints": [
                    [round(u["x"] + (op_cx - u["x"]) * 0.4 + offset),
                     round(u["y"] + (op_cy - u["y"]) * 0.4)],
                    [round(op_cx + offset * 0.5), round(op_cy)],
                ],
                "objective": f"OPFOR 격멸",
            })

    return {
        "reasoning": "[하네스 규칙 기반]",
        "mission_plans": plans,
    }


def _default_metrics() -> EpisodeMetrics:
    """에러 시 기본 메트릭 반환."""
    from datetime import datetime
    import uuid as _uuid
    return EpisodeMetrics(
        episode_id=_uuid.uuid4().hex[:12],
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        winner="draw",
        duration_ticks=0,
        blufor_initial=0,
        blufor_survived=0,
        opfor_initial=0,
        opfor_survived=0,
        blufor_survival_rate=0.0,
        opfor_elimination_rate=0.0,
        total_damage_dealt=0.0,
        total_damage_taken=0.0,
        combat_efficiency=1.0,
        detected_engagement_rate=0.0,
        surprise_received_count=0,
        mission_plans_applied=0,
        recon_conducted=False,
        last_plan={},
        events_summary=[],
    )
