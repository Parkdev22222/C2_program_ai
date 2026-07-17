"""
하네스 엔지니어링 컨트롤러.

에피소드 실행 → 메트릭 수집 → 규칙 추출 → DB 저장 → 파일 동기화의
전체 학습 루프를 관리합니다.
백그라운드 스레드로 n_episodes를 실행하며, 콜백을 통해 진행 상황을 알립니다.

[Task 26] wargame/harness/controller.py 에서 이동 (애플리케이션 계층).

HarnessDB(인프라)는 이 모듈에서 직접 생성하지 않는다 — Task 20의
EventStore DI 패턴과 동일하게, `HarnessStore` 포트(타입힌트 전용, TYPE_CHECKING)
와 모듈 레벨 팩토리(`_default_harness_db_factory` / `set_default_harness_db_factory()`)
를 통해 주입받는다. 레거시 shim(`wargame.harness`)이 기본 팩토리로
HarnessDB를 wiring 한다 (application → infrastructure import 금지).
"""

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional

from .episode_runner import EpisodeRunner
from .rule_extractor import RuleExtractor
from .rule_manager import RuleManager, SECTIONS

if TYPE_CHECKING:
    from c2.application.ports.harness_store import HarnessStore

logger = logging.getLogger(__name__)

# ── HarnessDB DI 팩토리 (의존성 역전) ─────────────────────────────────

_default_harness_db_factory: Optional[Callable[[], "HarnessStore"]] = None


def set_default_harness_db_factory(factory: Callable[[], "HarnessStore"]) -> None:
    """기본 HarnessDB 팩토리를 주입한다. (레거시 shim에서 wiring)"""
    global _default_harness_db_factory
    _default_harness_db_factory = factory


class HarnessController:
    """
    워게임 하네스 엔지니어링 컨트롤러.

    여러 에피소드를 순차 실행하며 전술 규칙을 자동으로 학습합니다.

    사용 예:
        controller = HarnessController(engine_factory)
        controller.start_training(n_episodes=10)
        # ...
        stats = controller.get_db_stats()
    """

    def __init__(
        self,
        engine_factory: Callable,
        agent=None,
        planner=None,
        db: "Optional[HarnessStore]" = None,
    ):
        """
        HarnessController 초기화.

        Args:
            engine_factory: callable() -> WargameEngine 팩토리 함수
            agent: BattlefieldAgent 인스턴스 (없으면 규칙 기반 폴백)
            planner: MissionPlanner 인스턴스 (없으면 간단한 규칙 적용)
            db: HarnessStore 구현체 (생략 시 주입된 기본 팩토리로 생성)
        """
        if db is None:
            if _default_harness_db_factory is None:
                raise RuntimeError(
                    "No HarnessDB configured; inject db or call "
                    "set_default_harness_db_factory()"
                )
            db = _default_harness_db_factory()
        self._db = db
        self._runner = EpisodeRunner(engine_factory, agent, planner)
        self._extractor = RuleExtractor(agent)
        self._rule_manager = RuleManager(self._db)

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._progress = {
            "current": 0,
            "total": 0,
            "last_metrics": None,
            "status": "idle",
        }

        logger.info("HarnessController 초기화 완료")

    def start_training(
        self,
        n_episodes: int = 10,
        replan_interval_ticks: int = 120,
        prune_every_n: int = 5,
        on_progress: Optional[Callable] = None,
    ):
        """
        백그라운드 스레드로 학습 루프를 시작합니다.

        Args:
            n_episodes: 실행할 에피소드 수
            replan_interval_ticks: 재계획 주기 (틱 단위)
            prune_every_n: 비효율 규칙 가지치기 주기 (에피소드 단위)
            on_progress: 에피소드 완료 시 호출할 콜백 함수
                         signature: (current: int, total: int, metrics: EpisodeMetrics)
        """
        if self._running:
            logger.warning("학습이 이미 실행 중입니다.")
            return

        self._running = True
        self._progress.update({
            "current": 0,
            "total": n_episodes,
            "last_metrics": None,
            "status": "starting",
        })

        self._thread = threading.Thread(
            target=self._training_loop,
            args=(n_episodes, replan_interval_ticks, prune_every_n, on_progress),
            daemon=True,
            name="HarnessTraining",
        )
        self._thread.start()
        logger.info(f"학습 시작: {n_episodes}개 에피소드, 재계획={replan_interval_ticks}틱")

    def stop_training(self):
        """실행 중인 학습 루프를 중지합니다."""
        self._running = False
        logger.info("학습 중지 요청")

    def get_progress(self) -> dict:
        """
        현재 학습 진행 상황을 반환합니다.

        Returns:
            current, total, last_metrics, status를 포함하는 딕셔너리
        """
        return dict(self._progress)

    def _training_loop(
        self,
        n_episodes: int,
        replan_interval_ticks: int,
        prune_every_n: int,
        on_progress: Optional[Callable],
    ):
        """
        학습 루프 메인 로직 (백그라운드 스레드에서 실행).

        각 에피소드마다:
        1. 에피소드 실행
        2. DB 저장
        3. 규칙 효과 업데이트
        4. 규칙 추출 및 추가
        5. 주기적 가지치기
        6. 파일 동기화
        7. 에이전트 지시사항 갱신
        """
        try:
            for i in range(n_episodes):
                if not self._running:
                    break

                self._progress.update({
                    "current": i + 1,
                    "total": n_episodes,
                    "status": "running",
                })
                logger.info(f"에피소드 {i + 1}/{n_episodes} 시작")

                # 1. 에피소드 실행
                try:
                    metrics = self._runner.run_episode(
                        replan_interval_ticks=replan_interval_ticks
                    )
                    self._progress["last_metrics"] = metrics.to_dict()
                except Exception as e:
                    logger.error(f"에피소드 실행 실패: {e}")
                    continue

                # 2. DB 저장
                try:
                    active_ids = self._rule_manager.get_active_rule_ids()
                    self._db.save_episode(metrics, active_ids)
                except Exception as e:
                    logger.error(f"에피소드 DB 저장 실패: {e}")

                # 3. 규칙 효과 업데이트
                try:
                    self._rule_manager.update_effectiveness(metrics.episode_id, metrics.winner)
                except Exception as e:
                    logger.warning(f"규칙 효과 업데이트 실패: {e}")

                # 4. 규칙 추출
                try:
                    new_rules = self._extractor.extract_rules(metrics)
                    added = 0
                    for rule_text, section, confidence in new_rules:
                        rule_id = self._rule_manager.add_rule(
                            rule_text, section, confidence, metrics.episode_id
                        )
                        if rule_id:
                            added += 1
                    if added > 0:
                        logger.info(f"새 규칙 추가: {added}개")
                except Exception as e:
                    logger.warning(f"규칙 추출 실패: {e}")

                # 4-b. 공간 패널티/보너스 존 업데이트
                try:
                    from c2.application.harness.tactical_memory import SpatialRuleExtractor, get_tactical_memory
                    spatial_extractor = SpatialRuleExtractor(get_tactical_memory())
                    spatial_result = spatial_extractor.analyze_episode(metrics)
                    p_added = spatial_result.get("penalty_zones_added", 0)
                    b_added = spatial_result.get("bonus_zones_added", 0)
                    if p_added + b_added > 0:
                        logger.info(f"공간 분석: 패널티 존 {p_added}개, 보너스 존 {b_added}개 업데이트")
                except Exception as e:
                    logger.warning(f"공간 분석 실패: {e}")

                # 5. 주기적 가지치기
                if (i + 1) % prune_every_n == 0:
                    try:
                        pruned = self._rule_manager.prune_ineffective()
                        if pruned > 0:
                            logger.info(f"비효율 규칙 가지치기: {pruned}개 비활성화")
                    except Exception as e:
                        logger.warning(f"규칙 가지치기 실패: {e}")

                # 6. 파일 동기화
                try:
                    self._rule_manager.sync_to_file()
                except Exception as e:
                    logger.warning(f"파일 동기화 실패: {e}")

                # 7. 에이전트 지시사항 갱신
                if self._runner._agent is not None:
                    try:
                        self._runner._agent.reload_instructions()
                    except Exception:
                        pass  # reload_instructions 미지원 시 무시

                # 8. 콜백 호출
                if on_progress:
                    try:
                        on_progress(i + 1, n_episodes, metrics)
                    except Exception as e:
                        logger.warning(f"on_progress 콜백 오류: {e}")

                logger.info(
                    f"에피소드 {i + 1}/{n_episodes} 완료: "
                    f"승자={metrics.winner}, "
                    f"BLUFOR생존율={metrics.blufor_survival_rate:.0%}"
                )

        except Exception as e:
            logger.error(f"학습 루프 오류: {e}")
        finally:
            final_status = "done" if self._running else "stopped"
            self._progress["status"] = final_status
            self._running = False
            logger.info(f"학습 루프 종료: status={final_status}")

    def get_db_stats(self) -> dict:
        """
        DB 통계를 반환합니다.

        Returns:
            총 에피소드 수, 승률, 활성 규칙 수 등
        """
        try:
            return self._db.get_stats()
        except Exception as e:
            logger.error(f"get_db_stats 오류: {e}")
            return {}

    def get_recent_episodes(self, n: int = 10) -> list:
        """
        최근 N개 에피소드 목록을 반환합니다.

        Args:
            n: 반환할 에피소드 수

        Returns:
            에피소드 딕셔너리 리스트
        """
        try:
            all_episodes = self._db.get_all_episodes()
            return all_episodes[-n:]
        except Exception as e:
            logger.error(f"get_recent_episodes 오류: {e}")
            return []

    def get_active_rules(self) -> dict:
        """
        섹션별로 분류된 활성 규칙을 반환합니다.

        Returns:
            {section_name: [rule_dict, ...]} 형태의 딕셔너리
        """
        try:
            return {
                section: self._db.get_active_rules(section=section)
                for section in SECTIONS
            }
        except Exception as e:
            logger.error(f"get_active_rules 오류: {e}")
            return {section: [] for section in SECTIONS}

    def get_tactical_stats(self) -> dict:
        """전술 메모리 통계 반환."""
        try:
            from c2.application.harness.tactical_memory import get_tactical_memory
            return get_tactical_memory().get_stats()
        except Exception:
            return {}

    def wait_for_completion(self, timeout: Optional[float] = None):
        """
        학습 완료까지 대기합니다.

        Args:
            timeout: 최대 대기 시간 (초, None이면 무제한)
        """
        if self._thread is not None:
            self._thread.join(timeout=timeout)
