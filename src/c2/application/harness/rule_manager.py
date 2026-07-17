"""
전술 규칙 관리 모듈.

DB에 저장된 전술 규칙의 추가, 효과 평가, 가지치기,
그리고 agent_custom_instructions.txt 파일과의 동기화를 담당합니다.

[Task 26] wargame/harness/rule_manager.py 에서 이동 (애플리케이션 계층).
HarnessDB(인프라)의 구체 타입 대신 `HarnessStore` 포트(타입힌트 전용,
TYPE_CHECKING)를 참조하여 런타임 의존을 없앤다
(application → infrastructure import 금지). 실제 HarnessDB 인스턴스는
호출자(HarnessController, DI 팩토리 경유)가 주입한다.
"""

import logging
from datetime import datetime
from pathlib import Path
from c2._paths import config_path
from typing import TYPE_CHECKING, Dict, List, Optional
from uuid import uuid4

if TYPE_CHECKING:
    from c2.application.ports.harness_store import HarnessStore

logger = logging.getLogger(__name__)

# agent_custom_instructions.txt 경로
INSTRUCTIONS_FILE = config_path("agent_custom_instructions.txt")

# 지원하는 섹션 목록
SECTIONS = ["RECON", "ATTACK", "EXECUTION", "LEARNED_RULES"]


class RuleManager:
    """
    전술 규칙의 전체 생명주기를 관리합니다.

    규칙 추가 / 효과 평가 / 비효율 규칙 가지치기 /
    agent_custom_instructions.txt 파일 동기화를 수행합니다.
    """

    def __init__(self, db: "HarnessStore"):
        """
        RuleManager 초기화.

        Args:
            db: HarnessStore 구현체 (HarnessDB 등)
        """
        self._db = db

    def add_rule(
        self,
        text: str,
        section: str,
        confidence: float,
        source_episode: str,
    ) -> Optional[str]:
        """
        새 전술 규칙을 추가합니다.

        중복 규칙은 추가하지 않습니다.

        Args:
            text: 규칙 텍스트
            section: 규칙 분류 (RECON / ATTACK / EXECUTION / LEARNED_RULES)
            confidence: 신뢰도 (0.0 ~ 1.0)
            source_episode: 출처 에피소드 ID

        Returns:
            새로 추가된 규칙 ID, 중복이면 None
        """
        # 섹션 정규화
        if section not in SECTIONS:
            section = "LEARNED_RULES"

        # 기존 규칙 텍스트 목록 조회 (중복 검사용)
        existing_rules = self._db.get_active_rules(section=section)
        existing_texts = [r["text"] for r in existing_rules]

        # 중복 검사
        if self._is_duplicate(text, existing_texts):
            logger.debug(f"중복 규칙 스킵: {text[:60]}")
            return None

        # 규칙 저장
        rule_id = uuid4().hex[:8]
        self._db.save_rule(rule_id, text, section, confidence, source_episode)
        logger.info(f"규칙 추가: {rule_id} [{section}] '{text[:60]}'")
        return rule_id

    def update_effectiveness(self, episode_id: str, winner: str):
        """
        특정 에피소드에서 활성화된 규칙들의 win/loss 카운트를 업데이트합니다.

        Args:
            episode_id: 에피소드 ID
            winner: 에피소드 승자 ("BLUFOR" / "OPFOR" / "draw")
        """
        try:
            episode = self._db.get_episode(episode_id)
            if episode is None:
                logger.warning(f"update_effectiveness: 에피소드 없음 {episode_id}")
                return

            active_rule_ids = episode.get("active_rules", [])
            self._db.update_rule_effectiveness(active_rule_ids, winner)
            logger.debug(
                f"규칙 효과 업데이트: 에피소드={episode_id}, 규칙={len(active_rule_ids)}개, 결과={winner}"
            )
        except Exception as e:
            logger.error(f"update_effectiveness 오류: {e}")

    def prune_ineffective(
        self,
        min_episodes: int = 5,
        min_win_rate: float = 0.35,
    ) -> int:
        """
        비효과적인 규칙을 비활성화합니다.

        min_episodes 이상 사용되었고 승률이 min_win_rate 미만인 규칙을 비활성화합니다.

        Args:
            min_episodes: 평가 최소 에피소드 수
            min_win_rate: 유지 최소 승률

        Returns:
            비활성화된 규칙 수
        """
        pruned = 0
        try:
            all_rules = self._db.get_active_rules()
            for rule in all_rules:
                win_count = rule.get("win_count", 0)
                loss_count = rule.get("loss_count", 0)
                total = win_count + loss_count

                if total < min_episodes:
                    continue  # 데이터 부족 → 유지

                win_rate = win_count / total
                if win_rate < min_win_rate:
                    self._db.deactivate_rule(rule["rule_id"])
                    pruned += 1
                    logger.info(
                        f"비효과적 규칙 비활성화: {rule['rule_id']} "
                        f"(승률={win_rate:.0%}, 총={total}회)"
                    )

        except Exception as e:
            logger.error(f"prune_ineffective 오류: {e}")

        return pruned

    def get_active_rule_ids(self) -> List[str]:
        """
        현재 활성화된 모든 규칙의 ID 목록을 반환합니다.

        Returns:
            규칙 ID 리스트
        """
        try:
            rules = self._db.get_active_rules()
            return [r["rule_id"] for r in rules]
        except Exception as e:
            logger.error(f"get_active_rule_ids 오류: {e}")
            return []

    def sync_to_file(self):
        """
        DB의 활성 규칙을 agent_custom_instructions.txt 파일에 반영합니다.

        [LEARNED_RULES] 섹션의 내용을 DB의 LEARNED_RULES 규칙으로 교체합니다.
        """
        try:
            # 기존 파일 읽기
            if INSTRUCTIONS_FILE.exists():
                content = INSTRUCTIONS_FILE.read_text(encoding="utf-8")
            else:
                logger.warning(f"sync_to_file: 파일 없음 {INSTRUCTIONS_FILE}")
                content = ""

            # DB에서 LEARNED_RULES 규칙 조회
            learned_rules = self._db.get_active_rules(section="LEARNED_RULES")

            # LEARNED_RULES 섹션 내용 생성
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            learned_lines = [
                f"[LEARNED_RULES]",
                f"# 자동 동기화: {timestamp}",
            ]
            for rule in learned_rules:
                conf = rule.get("confidence", 0.5)
                text = rule.get("text", "")
                learned_lines.append(f"- [conf={conf:.2f}] {text}")

            new_learned_section = "\n".join(learned_lines)

            # 파일에서 [LEARNED_RULES] 섹션 찾아 교체
            if "[LEARNED_RULES]" in content:
                # [LEARNED_RULES] 섹션 이후를 모두 교체
                idx = content.index("[LEARNED_RULES]")
                content = content[:idx] + new_learned_section + "\n"
            else:
                # 섹션 없으면 파일 끝에 추가
                content = content.rstrip() + "\n\n" + new_learned_section + "\n"

            # 파일 쓰기
            INSTRUCTIONS_FILE.write_text(content, encoding="utf-8")
            logger.info(
                f"agent_custom_instructions.txt 동기화 완료: "
                f"LEARNED_RULES {len(learned_rules)}개"
            )

        except Exception as e:
            logger.error(f"sync_to_file 오류: {e}")

    def _is_duplicate(self, text: str, existing: List[str], threshold: float = 0.8) -> bool:
        """
        자카드 유사도 기반 중복 규칙 검사.

        Args:
            text: 검사할 규칙 텍스트
            existing: 기존 규칙 텍스트 목록
            threshold: 중복 판단 임계값 (기본 0.8)

        Returns:
            True이면 중복 (버릴 것), False이면 새 규칙
        """
        words_new = set(text.split())
        if not words_new:
            return False

        for existing_text in existing:
            words_ex = set(existing_text.split())
            if not words_ex:
                continue
            intersection = len(words_new & words_ex)
            union = len(words_new | words_ex)
            if union == 0:
                continue
            similarity = intersection / union
            if similarity >= threshold:
                return True
        return False

    def get_stats(self) -> dict:
        """
        섹션별 규칙 수 및 전체 활성 규칙 수를 반환합니다.

        Returns:
            통계 딕셔너리
        """
        try:
            all_rules = self._db.get_active_rules()
            total = len(all_rules)
            by_section: Dict[str, int] = {}
            for section in SECTIONS:
                by_section[section] = sum(
                    1 for r in all_rules if r.get("section") == section
                )
            return {
                "total_active_rules": total,
                "by_section": by_section,
            }
        except Exception as e:
            logger.error(f"get_stats 오류: {e}")
            return {"total_active_rules": 0, "by_section": {}}
