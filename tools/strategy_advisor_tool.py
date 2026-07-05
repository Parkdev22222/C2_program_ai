"""
전략 어드바이저 툴 - EXAONE4와 EXAONE Deep 간의 듀얼 모델 오케스트레이션

동작 흐름:
1. EXAONE4가 군사 전략/전술 추천 쿼리 감지
2. EXAONE4가 strategy_advisor_tool 호출
3. 이 툴이 세션 메모리에서 EXAONE4의 이전 상황 분석 결과를 가져옴
4. EXAONE Deep에 [상황 분석 + 사용자 쿼리]를 전달하여 전략/전술 권고 생성
5. EXAONE Deep의 권고 결과를 EXAONE4에게 반환
6. EXAONE4가 자신의 상황 분석 + EXAONE Deep 권고를 종합하여 최종 응답
"""
import logging
from typing import Optional, Any
from smolagents import Tool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 세션 메모리: EXAONE4의 상황 분석 결과 저장소
# gradio_app.py에서 EXAONE4 응답 후 업데이트됨
# ─────────────────────────────────────────────
_session_memory: dict = {
    "situation_analysis": None,   # 가장 최근 EXAONE4 상황 분석 텍스트
    "analysis_timestamp": None,   # 분석 시각
}


def update_situation_memory(analysis_text: str, video_ids: list = None):
    """
    EXAONE4가 상황 분석을 완료한 후 호출하여 세션 메모리를 갱신합니다.
    gradio_app.py 또는 battlefield_agent.py에서 호출됩니다.
    (video_ids 인자는 하위 호환용으로만 남아 있으며 무시됩니다.)
    """
    from datetime import datetime
    _session_memory["situation_analysis"] = analysis_text
    _session_memory["analysis_timestamp"] = datetime.now().isoformat()
    logger.info("Situation memory updated with latest EXAONE4 analysis")


def get_situation_memory() -> dict:
    """현재 세션 메모리의 상황 분석 정보를 반환합니다."""
    return dict(_session_memory)


def clear_situation_memory():
    """세션 메모리를 초기화합니다."""
    _session_memory["situation_analysis"] = None
    _session_memory["analysis_timestamp"] = None


# ─────────────────────────────────────────────
# EXAONE Deep 모델 인스턴스 (전역 싱글톤)
# ─────────────────────────────────────────────
_strategy_model = None


def get_strategy_model():
    """EXAONE Deep 모델을 지연 로딩으로 가져옵니다."""
    global _strategy_model
    if _strategy_model is None:
        try:
            from agent.strategy_model_loader import load_strategy_model_from_config_file
            logger.info("Loading EXAONE Deep strategy model (lazy load)...")
            _strategy_model = load_strategy_model_from_config_file()
        except Exception as e:
            logger.error(f"Failed to load EXAONE Deep model: {e}")
            raise RuntimeError(f"EXAONE Deep 모델 로딩 실패: {e}")
    return _strategy_model


def set_strategy_model(model):
    """외부에서 주입한 EXAONE Deep 모델 인스턴스를 설정합니다 (테스트/초기화용)."""
    global _strategy_model
    _strategy_model = model


# ─────────────────────────────────────────────
# EXAONE Deep 프롬프트 빌더
# ─────────────────────────────────────────────

STRATEGY_SYSTEM_PROMPT = """당신은 EXAONE Deep 기반 군사 전략/전술 전문가 AI입니다.
제공된 전장 상황 분석 정보를 바탕으로 구체적이고 실행 가능한 군사 전략 및 전술을 추천합니다.

추천 시 다음 원칙을 준수하세요:
1. 제공된 상황 정보에 근거한 현실적인 권고만 제시
2. 아군 피해 최소화 원칙 우선
3. 단기/중기/장기 행동 계획으로 구분하여 제시
4. 각 전략/전술의 위험도와 효과를 평가하여 제시
5. 군사 전술 원칙(기습, 집중, 경제, 기동, 통일 등)을 명시적으로 적용"""

STRATEGY_USER_TEMPLATE = """## 전장 상황 분석 (EXAONE4 제공)

{situation_analysis}

---

## 병종 상성 및 추천 기동 경로 (워게임 시뮬레이터)

{tactical_recommendation}

---

## 전략/전술 추천 요청

{user_query}

위 상황 분석과 병종 상성·기동 경로 데이터를 종합하여 구체적인 군사 전략 및 전술을 추천해주세요."""

NO_SITUATION_TEMPLATE = """## 병종 상성 및 추천 기동 경로 (워게임 시뮬레이터)

{tactical_recommendation}

---

## 전략/전술 추천 요청

{user_query}

주의: 사전 상황 분석 결과가 없습니다. 워게임 전술 데이터와 일반 군사 원칙에 기반하여 답변합니다."""


class StrategyAdvisorTool(Tool):
    """
    EXAONE Deep 전략/전술 어드바이저 툴

    EXAONE4가 이 툴을 호출하면:
    - 세션 메모리에서 이전 상황 분석 결과를 가져옴
    - EXAONE Deep에 상황 분석 + 쿼리를 전달
    - EXAONE Deep의 전략/전술 권고 결과를 EXAONE4에게 반환
    """

    name = "strategy_advisor_tool"
    description = (
        "군사 전략 및 전술 추천을 위해 EXAONE Deep 전문 모델을 호출합니다. "
        "전략, 전술, 작전계획, 기동방안, 화력지원, 방어계획 등 군사적 행동 권고가 필요할 때 사용하세요. "
        "이 툴은 이전에 EXAONE4가 수행한 전장 상황 분석 결과를 자동으로 EXAONE Deep에 전달합니다."
    )
    inputs = {
        "query": {
            "type": "string",
            "description": (
                "전략/전술 추천 요청 내용. 예: '적 기갑부대 탐지 시 아군 보병 전술 추천', "
                "'현재 전장 상황에서 방어 거점 구축 방안', '포위 기동을 위한 작전 계획'"
            ),
        },
        "additional_context": {
            "type": "string",
            "description": "추가 맥락 정보 (선택 사항). 아군 전력, 지형 정보 등 보완 정보.",
            "nullable": True,
        },
    }
    output_type = "string"

    def _get_tactical_recommendation_text(self) -> str:
        """워게임 전술 추천(상성+경로)을 텍스트로 변환합니다."""
        try:
            from tools.wargame_strategy_tool import get_wargame_tactical_recommendation
            result = get_wargame_tactical_recommendation()
            if result.get("status") != "success":
                return f"[워게임 전술 데이터 없음: {result.get('message', result.get('status'))}]"

            lines = [f"게임 시각: {result.get('game_time', 'N/A')}"]

            matchups = result.get("matchup_recommendations", [])
            if matchups:
                lines.append("\n### 병종 상성 기반 교전 매칭")
                for m in matchups:
                    lines.append(
                        f"- {m['blufor_unit']}({m['blufor_type']}, CP:{m['blufor_cp']}) "
                        f"→ {m['recommended_target']}({m['target_type']}, CP:{m['target_cp']}) "
                        f"[{m['advantage']}] 화력배율x{m['firepower_multiplier']} "
                        f"거리:{m['distance_m']/1000:.1f}km | {m['reason']}"
                    )

            routes = result.get("movement_routes", [])
            if routes:
                lines.append("\n### 추천 기동 경로")
                for r in routes:
                    wp_str = " → ".join(f"({w[0]},{w[1]})" for w in r["waypoints"])
                    lines.append(
                        f"- {r['unit_id']}({r['unit_type']}) "
                        f"출발({r['from'][0]},{r['from'][1]}) → {r['to_target']}: "
                        f"{wp_str}\n  {r['terrain_notes']}"
                    )

            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Failed to get tactical recommendation: {e}")
            return "[워게임 전술 데이터 조회 실패]"

    def forward(self, query: str, additional_context: Optional[str] = None) -> str:
        logger.info(f"StrategyAdvisorTool called with query: {query[:100]}...")

        # 워게임 전술 추천 데이터 (상성 + 기동 경로)
        tactical_text = self._get_tactical_recommendation_text()
        logger.info("Tactical recommendation data fetched for EXAONE Deep prompt")

        # 세션 메모리에서 상황 분석 가져오기
        memory = get_situation_memory()
        situation_analysis = memory.get("situation_analysis")
        analysis_timestamp = memory.get("analysis_timestamp")

        # EXAONE Deep 프롬프트 구성
        if situation_analysis:
            context_note = ""
            if analysis_timestamp:
                context_note = f"\n(분석 시각: {analysis_timestamp})"
            if additional_context:
                situation_with_extra = (
                    f"{situation_analysis}{context_note}\n\n"
                    f"### 추가 맥락 정보\n{additional_context}"
                )
            else:
                situation_with_extra = f"{situation_analysis}{context_note}"

            user_content = STRATEGY_USER_TEMPLATE.format(
                situation_analysis=situation_with_extra,
                tactical_recommendation=tactical_text,
                user_query=query,
            )
        else:
            logger.warning("No situation analysis in memory. Using tactical-data-only mode.")
            user_content = NO_SITUATION_TEMPLATE.format(
                tactical_recommendation=tactical_text,
                user_query=query,
            )

        messages = [
            {"role": "system", "content": STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # EXAONE Deep 모델 호출
        try:
            strategy_model = get_strategy_model()
            gen_kwargs = getattr(strategy_model, "_strategy_generation_kwargs", {})
            temperature = gen_kwargs.get("temperature", 0.2)
            max_tokens = gen_kwargs.get("max_tokens", 8192)

            logger.info("Calling EXAONE Deep for strategy/tactics recommendation...")
            response = strategy_model(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            result = self._extract_text(response)
            logger.info("EXAONE Deep strategy response received successfully")
            return result

        except Exception as e:
            logger.error(f"EXAONE Deep call failed: {e}")
            return (
                f"[EXAONE Deep 전략 모델 호출 실패]\n"
                f"오류: {e}\n\n"
                f"이 툴을 재시도하지 마세요. "
                f"EXAONE Deep 조언 없이, 이미 확보한 툴 계산 결과"
                f"(get_wargame_situation, assess_recon_need, get_optimal_attack_positions 등)만으로 "
                f"최종 임무계획 JSON을 직접 생성하고 apply_wargame_mission_plan으로 즉시 적용하세요."
            )

    def _extract_text(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, list):
                parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
                return "".join(parts)
            return str(content)
        return str(response)


def create_strategy_advisor_tool(strategy_model=None) -> StrategyAdvisorTool:
    """
    StrategyAdvisorTool 인스턴스를 생성합니다.

    Args:
        strategy_model: 미리 로드된 EXAONE Deep 모델. None이면 지연 로딩.
    """
    if strategy_model is not None:
        set_strategy_model(strategy_model)
    return StrategyAdvisorTool()


# ─────────────────────────────────────────────
# 정찰 임무계획 어드바이저 (EXAONE Deep 검토)
# ─────────────────────────────────────────────

RECON_ADVISOR_SYSTEM_PROMPT = """당신은 EXAONE Deep 기반 군사 정찰 전술 전문가 AI입니다.
제안된 정찰 임무계획(경로, 부대 배치)을 검토하고 전술적 조언을 텍스트로 제시합니다.
JSON은 출력하지 마세요. 조언 텍스트만 작성하면 됩니다.

검토 기준:
1. 교전 회피 가능성 — 경로가 적 교전권(4km) 바깥을 유지하는가
2. 탐지 효율성 — 관측 포인트가 목표를 효과적으로 커버하는가
3. 부대 생존성 — 퇴로 확보 및 복귀 경로의 안전성
4. 임무 우선순위 — 위협도 높은 목표를 우선 정찰하는가

출력 형식(반드시 준수):
### 정찰 임무계획 검토 의견
[전술적 검토 내용]

### 수정 권고
[없으면 "원안 유지" 명시, 있으면 구체적 변경 내용을 텍스트로 서술]

### 종합 평가
[최종 전술적 평가 및 권고 사항]"""

RECON_ADVISOR_USER_TEMPLATE = """## 제안된 정찰 임무계획

### 경로 요약
{recon_summary}

### 상세 임무계획 JSON
```json
{recon_routes_json}
```

---

## 현재 전장 상황
{situation_analysis}

---

위 정찰 임무계획을 검토하고 전술적 조언을 텍스트로만 작성하세요.
JSON은 출력하지 않습니다. EXAONE4가 이 조언을 참고하여 최종 임무계획 JSON을 직접 생성합니다."""


class ReconAdvisorTool(Tool):
    """
    정찰 임무계획 EXAONE Deep 검토 도구

    recommend_recon_routes()가 생성한 정찰 경로를 EXAONE Deep에 넘겨
    전술적 조언 텍스트를 반환합니다.
    EXAONE4는 반환된 텍스트 조언과 초기 경로를 참고하여 최종 임무계획 JSON을 직접 생성하고
    apply_wargame_mission_plan()을 호출합니다.
    """

    name = "recon_advisor_tool"
    description = (
        "recommend_recon_routes()가 생성한 정찰 경로를 EXAONE Deep에게 전술 검토받고 "
        "전술적 조언 텍스트를 반환합니다. "
        "정찰 임무계획 수립 시 recommend_recon_routes() 호출 직후 이 툴을 사용하세요. "
        "반환된 텍스트 조언을 참고하여 EXAONE4가 최종 임무계획 JSON을 직접 생성합니다."
    )
    inputs = {
        "recon_routes_json": {
            "type": "string",
            "description": "recommend_recon_routes()가 반환한 apply_json 문자열 (JSON 형식)",
        },
        "recon_summary": {
            "type": "string",
            "description": "recommend_recon_routes()가 반환한 summary 문자열 (선택)",
            "nullable": True,
        },
        "tool_results_context": {
            "type": "string",
            "description": (
                "이전 툴 호출 결과 전체 (assess_recon_need, get_wargame_situation 등). "
                "가능한 한 모든 이전 툴 결과를 합쳐서 전달하면 EXAONE Deep의 조언 품질이 향상됩니다."
            ),
            "nullable": True,
        },
    }
    output_type = "string"

    def forward(self, recon_routes_json: str, recon_summary: str = None, tool_results_context: str = None) -> str:
        logger.info("ReconAdvisorTool called — forwarding recon routes to EXAONE Deep")

        # 현재 전장 상황 메모리 가져오기
        memory = get_situation_memory()
        situation_text = memory.get("situation_analysis") or "사전 상황 분석 없음."

        # 이전 툴 결과 전체를 상황 텍스트에 추가
        if tool_results_context:
            situation_text = situation_text + "\n\n## 이전 툴 호출 결과 전체\n" + tool_results_context

        user_content = RECON_ADVISOR_USER_TEMPLATE.format(
            recon_summary=recon_summary or "(요약 없음)",
            recon_routes_json=recon_routes_json,
            situation_analysis=situation_text,
        )

        messages = [
            {"role": "system", "content": RECON_ADVISOR_SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ]

        try:
            strategy_model = get_strategy_model()
            gen_kwargs = getattr(strategy_model, "_strategy_generation_kwargs", {})
            response = strategy_model(
                messages,
                temperature=gen_kwargs.get("temperature", 0.15),
                max_tokens=gen_kwargs.get("max_tokens", 4096),
            )
            advice_text = self._extract_text(response)
            logger.info("EXAONE Deep recon advice received")
            return advice_text
        except Exception as e:
            logger.error(f"EXAONE Deep call failed in ReconAdvisorTool: {e}")
            return (
                f"[EXAONE Deep 호출 실패: {e}]\n"
                f"이 툴을 재시도하지 마세요. "
                f"recommend_recon_routes()가 반환한 초기 경로(apply_json)를 그대로 사용하여 "
                f"최종 정찰 임무계획 JSON을 생성하고 apply_wargame_mission_plan으로 즉시 적용하세요."
            )

    def _extract_text(self, response) -> str:
        if isinstance(response, str):
            return response
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, list):
                return "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
            return str(content)
        return str(response)


def create_recon_advisor_tool(strategy_model=None) -> ReconAdvisorTool:
    if strategy_model is not None:
        set_strategy_model(strategy_model)
    return ReconAdvisorTool()
