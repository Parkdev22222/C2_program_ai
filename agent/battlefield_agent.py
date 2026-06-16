"""
C2 군사 AI 에이전트 - EXAONE4 기반 듀얼 모델 아키텍처
"""
import re
import yaml
import logging
from pathlib import Path
from typing import List, Optional, Any

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_agent_config() -> dict:
    with open(CONFIG_DIR / "agent_config.yaml") as f:
        return yaml.safe_load(f)


INSTRUCTIONS_FILE = CONFIG_DIR / "agent_custom_instructions.txt"


def _load_custom_instructions() -> str:
    if INSTRUCTIONS_FILE.exists():
        return INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    return ""


def get_instruction_section(section: str) -> str:
    """[SECTION_NAME] 헤더 아래의 규칙 줄을 추출합니다."""
    content = _load_custom_instructions()
    lines = content.splitlines()
    in_section = False
    result = []
    for line in lines:
        if line.strip() == f"[{section}]":
            in_section = True
            continue
        if in_section:
            if line.startswith("[") and line.endswith("]"):
                break
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                result.append(stripped)
    return "\n".join(result)


_MAX_LEARNED_RULES = 25  # 보관할 최근 학습 규칙 최대 개수


def append_learned_rule(rule: str) -> bool:
    """워게임 평가 후 학습된 규칙을 [LEARNED_RULES] 섹션에 추가합니다.

    [LEARNED_RULES] 섹션은 최근 _MAX_LEARNED_RULES 개만 보관한다.
    시스템 프롬프트 토큰 증가로 인한 LLM 추론 속도 저하를 방지하기 위함.
    """
    from datetime import datetime
    content = _load_custom_instructions()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_line = f"- [{timestamp}] {rule}"

    if "[LEARNED_RULES]" not in content:
        content += f"\n[LEARNED_RULES]\n# 에이전트가 워게임 평가 후 자동으로 추가하는 학습 규칙\n{new_line}"
        INSTRUCTIONS_FILE.write_text(content, encoding="utf-8")
        logger.info(f"Learned rule appended: {rule[:80]}")
        return True

    # [LEARNED_RULES] 섹션 분리
    pre, _, rest = content.partition("[LEARNED_RULES]")
    # rest = 섹션 헤더 이후 전체 텍스트 (다음 섹션까지 포함될 수 있으나 LEARNED_RULES는 파일 끝에 위치)
    existing_lines = [ln for ln in rest.splitlines() if ln.strip().startswith("- [")]
    existing_lines.append(new_line)
    # 최근 N개만 유지
    trimmed = existing_lines[-_MAX_LEARNED_RULES:]
    new_section = "[LEARNED_RULES]\n# 에이전트가 워게임 평가 후 자동으로 추가하는 학습 규칙\n" + "\n".join(trimmed)
    INSTRUCTIONS_FILE.write_text(pre + new_section, encoding="utf-8")
    logger.info(f"Learned rule appended ({len(trimmed)}/{_MAX_LEARNED_RULES}): {rule[:80]}")
    return True


def is_strategy_query(text: str, config: dict = None) -> bool:
    if config is None:
        cfg = _load_agent_config()
    else:
        cfg = config
    keywords = cfg.get("strategy_keywords", {})
    text_lower = text.lower()
    for lang_keywords in keywords.values():
        for kw in lang_keywords:
            if kw.lower() in text_lower:
                return True
    return False


def classify_intent(query: str) -> dict:
    """classify_intent를 mission_plan_validator에서 가져와 래핑합니다."""
    try:
        from tools.mission_plan_validator import classify_intent as _classify
        return _classify(query)
    except ImportError:
        return {"intent": "general", "requires_confirmation": False, "preferred_tools": []}


class BattlefieldAgent:
    """EXAONE4 기반 C2 군사 AI 에이전트"""

    def __init__(
        self,
        exaone4_model=None,
        strategy_model=None,
        videodb_manager=None,
        embedding_generator=None,
        pdf_rag_system=None,
    ):
        self._agent_config = _load_agent_config()
        self._custom_instructions = _load_custom_instructions()
        self._videodb_manager = videodb_manager
        self._embedding_generator = embedding_generator
        self._pdf_rag_system = pdf_rag_system

        self._selected_video_ids: List[str] = []
        self._selected_pdf_ids: List[str] = []

        self._exaone4_model = exaone4_model or self._load_exaone4()
        self._strategy_model = strategy_model or self._load_strategy_model()

        self._tools = self._build_tools()
        self._prepend_instructions = False

        self._agent = self._init_code_agent()

    def _load_exaone4(self):
        try:
            from agent.model_loader import load_model_from_config_file
            return load_model_from_config_file()
        except Exception as e:
            logger.error(f"Failed to load EXAONE4 model: {e}")
            raise

    def _load_strategy_model(self):
        try:
            from agent.strategy_model_loader import load_strategy_model_from_config_file
            return load_strategy_model_from_config_file()
        except Exception as e:
            logger.warning(f"Failed to load EXAONE Deep model: {e}. Strategy tool will fail if used.")
            return None

    def _build_tools(self) -> list:
        tools = []

        try:
            from tools.videodb_query_tool import (
                get_selected_contexts, query_video_semantic, query_video_by_object,
                query_video_by_event, get_video_summary, get_segment_details, set_active_videos,
            )
            tools.extend([get_selected_contexts, query_video_semantic, query_video_by_object,
                          query_video_by_event, get_video_summary, get_segment_details, set_active_videos])
            logger.info("VideoDB query tools loaded")
        except Exception as e:
            logger.warning(f"Failed to load videodb tools: {e}")

        try:
            from tools.pdf_rag_tool import pdf_rag_search, add_pdf_to_rag
            tools.extend([pdf_rag_search, add_pdf_to_rag])
            logger.info("PDF RAG tools loaded")
        except Exception as e:
            logger.warning(f"Failed to load PDF RAG tools: {e}")

        try:
            from tools.wargame_query_tool import (
                get_wargame_situation, get_wargame_unit_detail,
                get_wargame_battle_log, get_intelligence_report,
            )
            tools.extend([get_wargame_situation, get_wargame_unit_detail,
                          get_wargame_battle_log, get_intelligence_report])
            logger.info("Wargame simulator query tools loaded")
        except Exception as e:
            logger.warning(f"Failed to load wargame simulator query tools: {e}")

        try:
            from tools.wargame_mission_tool import (
                apply_wargame_mission_plan, apply_wargame_air_support, get_wargame_engine_status,
            )
            tools.extend([apply_wargame_mission_plan, apply_wargame_air_support, get_wargame_engine_status])
            logger.info("Wargame mission execution tools loaded")
        except Exception as e:
            logger.warning(f"Failed to load wargame mission tools: {e}")

        # validate/approve tool은 에이전트에서 제외 — [EXECUTION] 규칙상 dry_run=False 직접 적용
        # (등록 시 에이전트가 불필요하게 호출하여 step을 낭비하는 문제 방지)
        try:
            from tools.mission_plan_validator_tool import get_pending_plan_tool
            tools.append(get_pending_plan_tool)
            logger.info("Mission plan validator tools loaded (validate/approve excluded)")
        except Exception as e:
            logger.warning(f"Failed to load mission plan validator tools: {e}")

        try:
            from tools.coa_analysis_tool import analyze_coa_wargame
            tools.append(analyze_coa_wargame)
            logger.info("COA analysis tool loaded")
        except Exception as e:
            logger.warning(f"Failed to load COA analysis tool: {e}")

        try:
            from tools.wargame_strategy_tool import get_wargame_tactical_recommendation
            tools.append(get_wargame_tactical_recommendation)
            logger.info("Wargame tactical recommendation tool loaded")
        except Exception as e:
            logger.warning(f"Failed to load wargame strategy tool: {e}")

        try:
            from tools.wargame_opfor_routes_tool import predict_opfor_routes
            tools.append(predict_opfor_routes)
            logger.info("OPFOR predicted routes tool loaded")
        except Exception as e:
            logger.warning(f"Failed to load opfor routes tool: {e}")

        try:
            from tools.wargame_attack_advisor_tool import get_optimal_attack_positions
            tools.append(get_optimal_attack_positions)
            logger.info("Wargame attack position advisor tool loaded")
        except Exception as e:
            logger.warning(f"Failed to load attack advisor tool: {e}")

        try:
            from tools.wargame_recon_tool import assess_recon_need, recommend_recon_routes
            tools.extend([assess_recon_need, recommend_recon_routes])
            logger.info("Wargame recon tools loaded")
        except Exception as e:
            logger.warning(f"Failed to load recon tools: {e}")

        try:
            from tools.strategy_advisor_tool import create_strategy_advisor_tool
            strategy_tool = create_strategy_advisor_tool(self._strategy_model)
            tools.append(strategy_tool)
            logger.info("Strategy advisor tool (EXAONE Deep) loaded")
        except Exception as e:
            logger.warning(f"Failed to load strategy advisor tool: {e}")

        try:
            from tools.graph_rag_tool import graph_rag_military_query
            tools.append(graph_rag_military_query)
            logger.info("Graph RAG military ontology tool loaded")
        except Exception as e:
            logger.warning(f"Failed to load graph RAG tool: {e}")

        try:
            from tools.strategy_advisor_tool import create_recon_advisor_tool
            recon_advisor = create_recon_advisor_tool(self._strategy_model)
            tools.append(recon_advisor)
            logger.info("Recon advisor tool (EXAONE Deep route review) loaded")
        except Exception as e:
            logger.warning(f"Failed to load recon advisor tool: {e}")

        return tools

    def _init_code_agent(self):
        from smolagents import CodeAgent
        import inspect

        ca_cfg = self._agent_config.get("code_agent", {})
        valid_params = inspect.signature(CodeAgent.__init__).parameters

        kwargs = {
            "tools": self._tools,
            "model": self._exaone4_model,
            "max_steps": ca_cfg.get("max_steps", 20),
            "planning_interval": ca_cfg.get("planning_interval", 3),
            "additional_authorized_imports": ca_cfg.get("authorized_imports", []),
        }

        if "stream_outputs" in valid_params:
            kwargs["stream_outputs"] = ca_cfg.get("stream_outputs", False)

        agent = CodeAgent(**kwargs)

        if self._custom_instructions:
            self._append_custom_prompt(agent)

        self._patch_single_tool_guard(agent)
        self._patch_executor_output_limit(agent)
        self._patch_executor_timeout(agent)
        return agent

    def _patch_executor_timeout(self, code_agent, timeout_seconds: int = 300):
        """
        Python executor의 코드 블록 실행 타임아웃을 늘린다.

        smolagents 기본값은 30초인데, recon_advisor_tool / strategy_advisor_tool이
        EXAONE Deep 추론을 동기 호출하므로 쉽게 초과된다.
        max_execution_time 속성을 직접 패치하여 5분으로 늘린다.
        """
        for _exec_attr in ("python_executor", "python_interpreter", "_executor"):
            _exec = getattr(code_agent, _exec_attr, None)
            if _exec is None:
                continue
            try:
                cur = getattr(_exec, "max_execution_time", None)
                _exec.max_execution_time = timeout_seconds
                logger.info(
                    f"[ExecTimeout] {_exec_attr}.max_execution_time: {cur} → {timeout_seconds}s"
                )
            except Exception as e:
                logger.debug(f"[ExecTimeout] 설정 실패 ({_exec_attr}): {e}")
            break

    def _patch_executor_output_limit(self, code_agent, max_chars: int = 8000):
        """
        Python executor의 print() 출력 길이를 제한한다.

        smolagents는 코드 블록의 print() 출력 전체를 observation으로 기록하므로,
        긴 JSON 덤프 등이 무제한으로 누적되면 스텝마다 LLM 입력 토큰이 증가한다.
        max_print_outputs_length를 설정해 이를 방지한다.
        """
        for _exec_attr in ("python_executor", "python_interpreter", "_executor"):
            _exec = getattr(code_agent, _exec_attr, None)
            if _exec is None:
                continue
            try:
                cur = getattr(_exec, "max_print_outputs_length", None)
                if cur is None or cur > max_chars:
                    _exec.max_print_outputs_length = max_chars
                    logger.info(f"[OutputLimit] {_exec_attr}.max_print_outputs_length = {max_chars}")
            except Exception as e:
                logger.debug(f"[OutputLimit] 설정 실패 ({_exec_attr}): {e}")
            break

    def _patch_single_tool_guard(self, code_agent):
        """
        한 코드 블록(스텝)당 도구 호출을 1회로 제한한다.

        두 가지 패치를 적용한다:
        1) Python executor.__call__ 래핑 → 코드 블록 실행 전 카운터 리셋
        2) 각 Tool.forward() 래핑 → 2회째 호출 시 RuntimeError 발생
        """
        from tools.single_tool_guard import (
            guard as _guard,
            activate as _guard_activate,
            deactivate as _guard_deactivate,
            session_start as _guard_session_start,
        )

        # ── 1) executor 패치: 코드 블록 실행 구간만 가드 활성화 ──────────
        _patched_exec = False
        for _exec_attr in ("python_executor", "python_interpreter", "_executor"):
            _exec = getattr(code_agent, _exec_attr, None)
            if _exec is None:
                continue
            try:
                _orig_call = _exec.__call__

                def _guarded_exec(code, *a, _orig=_orig_call, **kw):
                    _guard_activate()
                    try:
                        return _orig(code, *a, **kw)
                    finally:
                        _guard_deactivate()

                _exec.__call__ = _guarded_exec
                logger.info(f"[SingleToolGuard] {_exec_attr}.__call__ 패치 완료")
                _patched_exec = True
            except Exception as e:
                logger.debug(f"[SingleToolGuard] executor 패치 실패 ({_exec_attr}): {e}")
            break

        if not _patched_exec:
            logger.warning("[SingleToolGuard] executor 패치 실패 — 카운터 리셋이 동작하지 않을 수 있음")

        # ── 2) Tool.forward() 래핑: 2회째 호출 시 거부 ───────────────────
        patched_count = 0
        for tool_obj in self._tools:
            if not hasattr(tool_obj, "forward"):
                continue
            try:
                _orig_forward = tool_obj.forward
                _tool_name = getattr(tool_obj, "name", repr(tool_obj))

                def _make_guarded_forward(fn, name):
                    def _guarded(*args, **kwargs):
                        _guard(name)
                        return fn(*args, **kwargs)
                    _guarded.__name__ = fn.__name__ if hasattr(fn, "__name__") else name
                    return _guarded

                tool_obj.forward = _make_guarded_forward(_orig_forward, _tool_name)
                patched_count += 1
            except Exception as e:
                logger.debug(f"[SingleToolGuard] tool.forward 패치 실패 ({getattr(tool_obj, 'name', '?')}): {e}")

        logger.info(f"[SingleToolGuard] {patched_count}/{len(self._tools)} 도구 forward() 패치 완료")

        # ── 3) agent.run() 래핑: agent.run() 시작 시 세션 레벨 추적 초기화 ──
        _orig_run = code_agent.run

        def _session_run(*args, **kwargs):
            _guard_session_start()
            return _orig_run(*args, **kwargs)

        code_agent.run = _session_run
        logger.info("[SingleToolGuard] agent.run() 세션 레벨 추적 패치 완료")

    def _append_custom_prompt(self, agent):
        try:
            pt = agent.prompt_templates
            if isinstance(pt, dict):
                existing = pt.get("system_prompt", "")
                pt["system_prompt"] = existing + "\n\n" + self._custom_instructions
                logger.info("Custom instructions appended to prompt_templates dict")
                return
            if hasattr(pt, "system_prompt"):
                existing = getattr(pt, "system_prompt", "") or ""
                pt.system_prompt = existing + "\n\n" + self._custom_instructions
                logger.info("Custom instructions appended to prompt_templates.system_prompt")
                return
        except Exception as e:
            logger.debug(f"prompt_templates append failed: {e}")

        logger.info("Falling back to per-query instruction prepend")
        self._prepend_instructions = True

    def reset_memory(self) -> None:
        """
        에이전트의 메모리·스텝 로그·Python 네임스페이스를 완전히 초기화한다.

        smolagents는 agent.run() 호출 사이에도 step_logs/memory가 누적되어
        LLM 입력 토큰이 점차 증가한다. 임무계획 실행 전에 호출하면 속도 저하를 방지한다.
        """
        inner = self._agent
        # 1) 신규 smolagents API — memory.reset()
        try:
            if hasattr(inner, "memory") and hasattr(inner.memory, "reset"):
                inner.memory.reset()
                logger.debug("[reset_memory] memory.reset() 완료")
        except Exception as e:
            logger.debug(f"[reset_memory] memory.reset() 실패: {e}")

        # 2) 구버전 smolagents — logs / step_logs 직접 클리어
        for _attr in ("logs", "step_logs", "_logs"):
            _obj = getattr(inner, _attr, None)
            if isinstance(_obj, list):
                _obj.clear()
                logger.debug(f"[reset_memory] {_attr} cleared")
                break

        # 3) Python executor 네임스페이스 클리어 (이전 실행 변수 제거)
        for _exec_attr in ("python_executor", "python_interpreter", "_executor"):
            _exec = getattr(inner, _exec_attr, None)
            if _exec is None:
                continue
            for _ns_attr in ("state", "_state", "local_vars", "_local_vars"):
                _ns = getattr(_exec, _ns_attr, None)
                if isinstance(_ns, dict):
                    _ns.clear()
                    logger.debug(f"[reset_memory] {_exec_attr}.{_ns_attr} cleared")
                    break
            break

        # 4) 대형 객체 GC 강제 수거
        try:
            import gc
            gc.collect()
        except Exception:
            pass

    def run(self, query: str, reset: bool = False) -> str:
        intent_result = classify_intent(query)
        intent = intent_result.get("intent", "general")
        preferred_tools = intent_result.get("preferred_tools", [])
        requires_confirmation = intent_result.get("requires_confirmation", False)

        logger.info(f"Intent: {intent}, requires_confirmation: {requires_confirmation}, preferred_tools: {preferred_tools}")

        augmented_query = self._augment_by_intent(query, intent, preferred_tools, requires_confirmation)

        if self._prepend_instructions and self._custom_instructions:
            augmented_query = (
                f"[시스템 지시사항]\n{self._custom_instructions}\n\n"
                f"[사용자 쿼리]\n{augmented_query}"
            )
            self._prepend_instructions = False

        logger.info(f"Running agent with query: {query[:80]}...")
        result = self._agent.run(augmented_query, reset=reset)
        return str(result)

    def _augment_by_intent(self, query, intent, preferred_tools, requires_confirmation):
        # gradio_app.py가 이미 규칙을 삽입한 쿼리는 재삽입하지 않는다.
        _already_has_rules = (
            "[RECON 규칙]" in query
            or "[ATTACK 규칙]" in query
            or "[EXECUTION 규칙]" in query
        )
        if _already_has_rules:
            return query

        execution_rules = get_instruction_section("EXECUTION")
        recon_rules = get_instruction_section("RECON")
        attack_rules = get_instruction_section("ATTACK")
        strategy_rules = get_instruction_section("STRATEGY")
        learned_rules = get_instruction_section("LEARNED_RULES")
        learned_suffix = f"\n\n[학습된 규칙]\n{learned_rules}" if learned_rules else ""

        if intent == "execution_request":
            return (
                f"{query}\n\n[커스텀 지시 — EXECUTION]\n{execution_rules}{learned_suffix}"
            )

        if intent in ("attack_planning", "general_strategy_advice", "planning_request"):
            return (
                f"{query}\n\n[커스텀 지시 — STRATEGY]\n{strategy_rules}{learned_suffix}"
            )

        if intent == "recon_planning":
            return (
                f"{query}\n\n[커스텀 지시 — RECON]\n{recon_rules}{learned_suffix}"
            )

        if is_strategy_query(query, self._agent_config):
            return (
                f"{query}\n\n[커스텀 지시 — STRATEGY]\n{strategy_rules}{learned_suffix}"
            )

        return query

    def set_video_context(self, video_ids: List[str]):
        self._selected_video_ids = video_ids
        try:
            from tools.videodb_query_tool import set_selected_video_ids
            set_selected_video_ids(video_ids)
        except Exception as e:
            logger.warning(f"Failed to update videodb tool context: {e}")
        logger.info(f"Video context set: {video_ids}")

    def set_pdf_context(self, pdf_ids: List[str]):
        self._selected_pdf_ids = pdf_ids
        try:
            from tools.pdf_rag_tool import set_selected_pdfs
            set_selected_pdfs(pdf_ids)
        except Exception as e:
            logger.warning(f"Failed to update PDF tool context: {e}")
        logger.info(f"PDF context set: {pdf_ids}")

    def add_pdf(self, pdf_path: str) -> str:
        try:
            from tools.pdf_rag_tool import add_pdf_to_rag
            result = add_pdf_to_rag(pdf_path)
            return str(result)
        except Exception as e:
            logger.error(f"Failed to add PDF: {e}")
            return f"PDF 추가 실패: {e}"

    def get_situation_memory(self) -> dict:
        from tools.strategy_advisor_tool import get_situation_memory
        return get_situation_memory()

    def reload_instructions(self):
        """agent_custom_instructions.txt를 재로드하여 에이전트 지시사항을 갱신합니다."""
        self._custom_instructions = _load_custom_instructions()
        # CodeAgent의 system_prompt도 갱신
        try:
            pt = self._agent.prompt_templates
            if isinstance(pt, dict):
                existing = pt.get("system_prompt", "")
                # 기존 커스텀 지시사항 제거 후 새 것으로 교체
                if "\n\n[시스템 지시사항]\n" in existing:
                    base = existing.split("\n\n[시스템 지시사항]\n")[0]
                else:
                    base = existing
                if self._custom_instructions:
                    pt["system_prompt"] = base + "\n\n[시스템 지시사항]\n" + self._custom_instructions
            elif hasattr(pt, "system_prompt"):
                existing = getattr(pt, "system_prompt", "") or ""
                if "\n\n[시스템 지시사항]\n" in existing:
                    base = existing.split("\n\n[시스템 지시사항]\n")[0]
                else:
                    base = existing
                if self._custom_instructions:
                    pt.system_prompt = base + "\n\n[시스템 지시사항]\n" + self._custom_instructions
            logger.info("Instructions reloaded successfully")
        except Exception as e:
            logger.warning(f"Failed to update agent prompt_templates: {e}")

    @property
    def agent(self):
        return self._agent

    @property
    def exaone4_model(self):
        return self._exaone4_model

    @property
    def strategy_model(self):
        return self._strategy_model
