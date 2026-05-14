"""
에피소드 결과로부터 전술 규칙을 추출하는 모듈.

LLM 에이전트가 있으면 프롬프트 기반으로 규칙을 추출하고,
없으면 규칙 기반 폴백 로직을 사용합니다.
"""

import logging
import re
from typing import List, Optional, Tuple

from .metrics import EpisodeMetrics

logger = logging.getLogger(__name__)

# 지원하는 섹션 태그
_SECTION_TAGS = {
    "RECON": "RECON",
    "ATTACK": "ATTACK",
    "EXECUTION": "EXECUTION",
    "LEARNED_RULES": "LEARNED_RULES",
}


class RuleExtractor:
    """
    에피소드 메트릭을 분석하여 전술 규칙을 추출합니다.

    LLM 에이전트가 제공되면 프롬프트 기반으로 규칙을 생성하고,
    그렇지 않으면 메트릭 기반 규칙 기반 폴백을 사용합니다.
    """

    def __init__(self, agent=None):
        """
        RuleExtractor 초기화.

        Args:
            agent: BattlefieldAgent 인스턴스 (없으면 규칙 기반 폴백 사용)
        """
        self._agent = agent

    def extract_rules(
        self, metrics: EpisodeMetrics
    ) -> List[Tuple[str, str, float]]:
        """
        에피소드 메트릭에서 전술 규칙을 추출합니다.

        Args:
            metrics: EpisodeMetrics 인스턴스

        Returns:
            (rule_text, section, confidence) 튜플 리스트
            section: "RECON" | "ATTACK" | "EXECUTION" | "LEARNED_RULES"
        """
        rules = []

        # LLM 추출 시도
        if self._agent is not None:
            try:
                rules = self._extract_with_llm(metrics)
                if rules:
                    logger.info(f"LLM 기반 규칙 추출: {len(rules)}개")
                    return rules
            except Exception as e:
                logger.warning(f"LLM 규칙 추출 실패, 폴백 사용: {e}")

        # 규칙 기반 폴백
        rules = self._extract_fallback(metrics)
        logger.info(f"규칙 기반 폴백 추출: {len(rules)}개")
        return rules

    def _build_prompt(self, metrics: EpisodeMetrics) -> str:
        """LLM 호출용 프롬프트 생성."""
        # 이벤트 텍스트 구성
        events_text = "\n".join(
            f"  [{ev.get('type', '')}] {ev.get('message', '')}"
            for ev in metrics.events_summary[:8]
        ) or "  (이벤트 없음)"

        game_time_ticks = metrics.duration_ticks

        return f"""[에피소드 결과 분석 - 전술 규칙 추출]

게임 결과: {metrics.winner} | 게임시간: {game_time_ticks}틱
BLUFOR 생존율: {metrics.blufor_survival_rate:.0%} | 교환비: {metrics.combat_efficiency:.1f}
정찰 수행: {metrics.recon_conducted} | 탐지 후 교전율: {metrics.detected_engagement_rate:.0%}
기습 피격 횟수: {metrics.surprise_received_count}

주요 전투 이벤트:
{events_text}

규칙 추출 지시:
- "- [RECON] <정찰 관련 규칙>" 형식으로 정찰 규칙
- "- [ATTACK] <공격 관련 규칙>" 형식으로 공격 규칙
- "- [EXECUTION] <실행 관련 규칙>" 형식으로 실행 규칙
- 수치(km, %, 틱)를 포함한 구체적 규칙
- 승리 요인 2~3개, 개선 필요 1~2개
- 총 3~5개 규칙만 출력"""

    def _extract_with_llm(
        self, metrics: EpisodeMetrics
    ) -> List[Tuple[str, str, float]]:
        """LLM 에이전트를 통해 규칙 추출."""
        prompt = self._build_prompt(metrics)
        raw_response = self._agent.run(prompt, reset=False)
        return self._parse_llm_response(str(raw_response), metrics)

    def _parse_llm_response(
        self, response: str, metrics: EpisodeMetrics
    ) -> List[Tuple[str, str, float]]:
        """LLM 응답에서 규칙 파싱."""
        rules = []
        lines = response.splitlines()

        for line in lines:
            line = line.strip()
            if not line.startswith("-"):
                continue

            # 섹션 태그 추출: [RECON], [ATTACK], [EXECUTION]
            tag_match = re.search(r"\[(RECON|ATTACK|EXECUTION|LEARNED_RULES)\]", line)
            if tag_match:
                section = tag_match.group(1)
                # 태그 이후 텍스트를 규칙으로 사용
                rule_text = line[tag_match.end():].strip()
                # 앞의 "- " 제거
                rule_text = re.sub(r"^[-\s]+", "", rule_text)
            else:
                section = "LEARNED_RULES"
                rule_text = re.sub(r"^[-\s]+", "", line)

            rule_text = rule_text.strip()

            # 품질 필터: 너무 짧은 규칙 제거
            if len(rule_text) < 10:
                continue

            # 신뢰도 계산
            confidence = self._calc_confidence(rule_text, metrics)
            rules.append((rule_text, section, confidence))

        return rules

    def _calc_confidence(self, rule_text: str, metrics: EpisodeMetrics) -> float:
        """규칙 텍스트와 메트릭을 바탕으로 신뢰도 계산."""
        confidence = 0.5

        # 수치 포함 시 +0.1
        if re.search(r"\d+(\.\d+)?\s*(km|%|틱|m|초)", rule_text):
            confidence += 0.1

        # 게임 승리 시 +0.2
        if metrics.winner == "BLUFOR":
            confidence += 0.2

        return min(1.0, confidence)

    def _extract_fallback(
        self, metrics: EpisodeMetrics
    ) -> List[Tuple[str, str, float]]:
        """
        메트릭 기반 규칙 추출 (폴백).

        주요 지표를 분석하여 기본 전술 규칙을 생성합니다.
        """
        rules = []

        # 생존율 낮음 → 정찰 강화 권장
        if metrics.blufor_survival_rate < 0.5:
            rule_text = (
                f"정찰 강화 필요: 기습 피격 {metrics.surprise_received_count}회 발생, "
                "정찰 우선 실시 권장"
            )
            confidence = 0.5
            if metrics.winner == "BLUFOR":
                confidence += 0.2
            rules.append((rule_text, "RECON", confidence))

        # 교환비 높음 → 현 공격 패턴 유지 권장
        if metrics.combat_efficiency > 2.0:
            rule_text = (
                f"현재 공격 패턴 효과적: 교환비 {metrics.combat_efficiency:.1f} 유지 권장"
            )
            confidence = 0.6
            if metrics.winner == "BLUFOR":
                confidence += 0.2
            rules.append((rule_text, "ATTACK", confidence))

        # 교환비 낮음 → 직접 교전 자제 권장
        if metrics.combat_efficiency < 0.5:
            rule_text = "직접 교전 자제, 측방 우회 기동 적용 권장"
            confidence = 0.5
            rules.append((rule_text, "ATTACK", confidence))

        # 기습 피격 많음 → 정찰 선행 강화
        if metrics.surprise_received_count >= 3:
            rule_text = (
                f"교전 전 반드시 정찰 실시: 기습 피격 {metrics.surprise_received_count}회 발생, "
                "탐지 상실 구역 정찰 선행 필요"
            )
            confidence = 0.55
            rules.append((rule_text, "RECON", confidence))

        # 정찰 미실시 + 높은 기습 피격
        if not metrics.recon_conducted and metrics.surprise_received_count > 0:
            rule_text = (
                "임무계획 수립 전 assess_recon_need() 호출 필수: "
                "정찰 미실시로 기습 피해 증가"
            )
            confidence = 0.6
            rules.append((rule_text, "EXECUTION", confidence))

        # OPFOR 격멸율 높음 → 승리 패턴 유지
        if metrics.opfor_elimination_rate > 0.6 and metrics.winner == "BLUFOR":
            rule_text = (
                f"OPFOR 격멸율 {metrics.opfor_elimination_rate:.0%} 달성: "
                "집중 화력 및 공세 기동 패턴 유지 권장"
            )
            confidence = 0.7
            rules.append((rule_text, "ATTACK", confidence))

        # 탐지 후 교전율 낮음 → 탐지 우선
        if metrics.detected_engagement_rate < 0.3 and metrics.recon_conducted:
            rule_text = (
                "탐지된 적 위치에서만 교전: 탐지 후 교전율 "
                f"{metrics.detected_engagement_rate:.0%}로 개선 필요"
            )
            confidence = 0.5
            rules.append((rule_text, "ATTACK", confidence))

        # 게임 승리 시 신뢰도 전체 보너스 적용
        if metrics.winner == "BLUFOR":
            rules = [(text, sec, min(1.0, conf + 0.1)) for text, sec, conf in rules]

        return rules

    def _is_duplicate(self, text: str, existing: List[str], threshold: float = 0.8) -> bool:
        """
        자카드 유사도를 이용한 중복 규칙 검사.

        Args:
            text: 검사할 규칙 텍스트
            existing: 기존 규칙 텍스트 목록
            threshold: 중복 판단 임계값 (기본 0.8)

        Returns:
            True이면 중복 (버릴 것), False이면 새 규칙
        """
        words_new = set(text.split())
        for existing_text in existing:
            words_ex = set(existing_text.split())
            intersection = len(words_new & words_ex)
            union = len(words_new | words_ex)
            if union == 0:
                continue
            similarity = intersection / union
            if similarity >= threshold:
                return True
        return False
