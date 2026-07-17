# Gradio God-Object 분해 Implementation Plan (Slice 4 상세)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) 추적.

**Goal:** `ui/gradio_app.py`(2,498줄, 워게임 세션 오케스트레이션 god object)를 application 계층으로 분해하고, `ui/web_api.py`(FastAPI 백엔드)를 추출된 오케스트레이션에 직접 배선한 뒤 Gradio UI를 삭제한다.

**Architecture:** gradio_app의 "계산·오케스트레이션"(엔진 생명주기·탐지워커·자동재계획·세션ops·하니스)을 `c2.application.simulation`으로 추출하고, Gradio 고유 "렌더링"(plotly figure·CSS·gr.Blocks)은 삭제한다. web_api는 `_ga().xxx()` 위임을 application 세션 직접 호출로 교체한다. composition root가 세션의 의존성(EventStore·온톨로지 스토어·advisor·agent·툴 엔진등록)을 wiring한다.

**Tech Stack:** Python 3, pytest, import-linter, FastAPI(web_api), plotly(현재 gradio 렌더).

## Global Constraints
- 좌표 미터(m) 정수. 동작 보존 원칙(단, Gradio UI는 의도적 삭제).
- **엔진 자동 재계획 콜백 4종**(`on_new_opfor_detection`·`on_blufor_cp_threshold`·`on_blufor_air_hit`·`on_target_moved`)은 반드시 유지·재등록 (CLAUDE.md 금지사항).
- import-linter `PYTHONPATH=src lint-imports` → 3 kept, 0 broken 유지. application은 outward import 금지.
- **검증 제약(중요): 이 환경엔 fastapi·gradio 미설치.** 따라서 (a) 추출된 application 세션/replan은 엔진으로 **런타임 테스트 가능**, (b) web_api 재배선·Gradio 삭제는 **구조적 리뷰 + 세션 단위테스트로만** 검증(런타임 스모크는 fastapi 있는 환경에서). 각 태스크는 이 한계를 명시.
- 각 Task 독립 커밋. 매 커밋 green(전체 pytest + lint 3 kept).

## gradio_app.py 분류 (인벤토리 기반)

**→ application (추출, 생존 필수, web_api 사용):**
- 엔진 생명주기: `_wg_ensure_engine`, `_wg_register_engine`(8개 툴 register_wargame_engine), `_wg_ensure_ontology`, `_ontology_flush`, `_get_recon_unit_ids`
- 플랜 적용: `_convert_latlon_plan_to_meters`, `_apply_plan_to_engine`, `_build_plan_repair_query`, `_apply_plan_with_repair`, `_apply_chat_plan_if_any`
- 탐지/이벤트: `_detection_queue`, `_detection_enqueue`/`_cp_threshold_enqueue`/`_air_hit_enqueue`/`_target_moved_enqueue`, `_execute_auto_attack_plan`(518-880), `_detection_worker`
- 세션 ops(web_api 호출): `wargame_apply_custom_scenario`, `wargame_start_pause`, `wargame_reset_sim`, `wargame_set_timescale`, `wargame_request_recon_plan`, `wargame_request_attack_plan`, `wg_chat_send`, `wargame_evaluate_and_learn`, `chat`, `_wg_status_text`
- 하니스: `_init_harness_controller`, `harness_start_training`/`get_status`/`stop_training`/`get_rules`
- 상황메모리: `_get_agent`, `_is_situation_analysis_response`, `_update_situation_memory_if_needed`, `_build_situation_query`
- 상태 전역: `_agent`, `_wg_engine`, `_wg_planner`, `_harness_controller`, `_wg_graph_store`, `_wg_ontology_writer`, `_auto_plan_lock`, `_auto_plan_status`

**→ 삭제 (Gradio UI/렌더링):**
- figure: `_build_map_figure`, `get_battlefield_map`, `_build_wargame_map`, `_build_damage_chart`, `_build_opfor_alert`, `_MARKER_*`
- UI 수명주기: `wargame_refresh`, `wargame_on_load`, `wargame_refresh_with_alert`, `_MSIS_CSS`, `create_app`, `launch_app`
- UI 상태파일: `_save_ui_state`/`_load_ui_state`/`_save_chat_history`/`_load_chat_history`/`_load_ui_config` (web_api가 쓰면 세션으로, 아니면 삭제)

**핵심 관찰:** web_api는 세션함수의 Gradio 튜플 `(label, fig, damage_fig, status, log)`에서 **데이터(label/status/log)만 쓰고 figure는 버린다.** → 세션 메서드는 **데이터(dict)만 반환**하도록 리팩터하고, figure 생성은 Gradio 쪽(삭제 대상)에만 남긴다.

---

## Task 29A: WargameSession 골격 + 엔진 생명주기 → application

**Files:** Create `src/c2/application/simulation/session.py`, `tests/application/test_session_lifecycle.py`. Modify `ui/gradio_app.py`(추출부를 세션에 위임).

**Design:** `class WargameSession` — 엔진/플래너/온톨로지/탐지큐 상태를 인스턴스가 소유. 의존성 주입: `__init__(self, *, agent=None, graph_store_factory=None, engine_factory=None, tool_register_hook=None)`. gradio의 모듈 전역 대신 세션 인스턴스가 상태를 가진다. 단, 하위호환을 위해 gradio_app은 모듈-레벨 싱글턴 `_session`을 만들어 기존 전역 함수가 `_session.xxx()`에 위임.

- [ ] Step 1: `tests/application/test_session_lifecycle.py` 작성 — `WargameSession(engine_factory=lambda: WargameEngine(setup_bn_vs_bn(), db=WargameDB(tmp)))`; `ensure_engine()`가 엔진 반환·콜백 4종 등록 확인; `reset()`·`set_timescale()`·`start_pause()`가 **데이터 dict** 반환(figure 아님). 실행 → FAIL.
- [ ] Step 2: `session.py`에 엔진 생명주기 이식 (`_wg_ensure_engine`→`ensure_engine`, `_wg_register_engine`→`register_engine`, `_wg_ensure_ontology`→`ensure_ontology`). 콜백 4종 등록 로직 그대로. application 계층이므로 WargameEngine/scenario/store는 **주입된 factory 경유**(직접 infra import 금지).
- [ ] Step 3: gradio_app에 `_session = WargameSession(...)` 싱글턴; `_wg_ensure_engine()` 등 기존 전역을 `_session.ensure_engine()` 위임 shim으로 축소.
- [ ] Step 4: 세션 테스트 PASS.
- [ ] Step 5: 전체 pytest green + `PYTHONPATH=src lint-imports` 3 kept. (gradio 미설치 → gradio_app import는 이 환경서 실패할 수 있음; 세션 테스트는 gradio 없이 통과해야 함 — 세션은 gradio 비의존.)
- [ ] Step 6: Commit `feat(application): WargameSession 골격 + 엔진 생명주기 (session.py)`.

## Task 29B: 자동 재계획 워커 → application.simulation.replan

**Files:** Create `src/c2/application/simulation/replan.py`, `tests/application/test_replan_worker.py`. Modify `session.py`(replan 배선), `ui/gradio_app.py`.

**Design:** `_execute_auto_attack_plan`(362줄) + `_detection_worker` + enqueue 4종을 replan.py로 이식. 세션과 큐를 통해 협력. `_xy_to_latlon`은 `c2.domain.wargame.coordinates` 사용.

- [ ] Step 1: `test_replan_worker.py` — 가짜 agent 주입 세션에서 detection 이벤트 enqueue → 워커가 재계획 경로 실행(agent.run 호출)됨을 확인(모의). 콜백 4종이 세션 엔진에 등록돼 이벤트가 큐로 들어감 검증. 실행 → FAIL.
- [ ] Step 2: replan 로직 이식(로직 verbatim, `_wg_engine` 전역참조→세션 참조, coord→domain). 콜백 재등록은 `reset` 시에도 유지(CLAUDE.md 규칙).
- [ ] Step 3: gradio_app 전역 enqueue/worker를 세션/replan 위임으로 축소.
- [ ] Step 4~6: 테스트 PASS, 전체 green+lint, Commit `feat(application): 자동 재계획 워커 → replan.py`.

## Task 29C: 세션 ops(플랜적용·정찰·공격·채팅·평가) → 세션 메서드(데이터 반환)

**Files:** Modify `session.py`(메서드 추가), Create `tests/application/test_session_ops.py`. Modify `ui/gradio_app.py`.

- [ ] Step 1: 테스트 — `session.apply_custom_scenario(cfg)`, `session.request_recon_plan()`/`request_attack_plan()`, `session.chat(msg)`, `session.evaluate_and_learn()`가 **dict** 반환(Gradio 튜플 아님); plan-apply-with-repair 로직 보존. (agent 주입, 엔진 tmp DB.) 실행 → FAIL.
- [ ] Step 2: 해당 함수들 세션 메서드로 이식; figure 생성 코드는 제거(gradio에만 잔류), 데이터(label/status/log/plan)만 반환. `_apply_plan_*` 헬퍼 이식.
- [ ] Step 3: gradio 전역을 세션 위임 shim + gradio는 세션 dict로 figure 렌더.
- [ ] Step 4~6: 테스트 PASS, green+lint, Commit `feat(application): 세션 ops(정찰·공격·채팅·평가) 데이터 반환`.

## Task 29D: 하니스 세션 ops → application

**Files:** Modify `session.py` 또는 Create `src/c2/application/harness/session.py`; `tests/application/test_harness_session.py`.

- [ ] `_init_harness_controller`/`harness_start_training`/`get_status`/`stop_training`/`get_rules`를 application으로(HarnessStore DI 기존 활용); 데이터 반환. 테스트·green+lint·Commit.

## Task 30: web_api → c2.presentation.web.api + 세션 직접 배선

**Files:** Create `src/c2/presentation/web/__init__.py`, `src/c2/presentation/web/api.py`; `ui/web_api.py`를 shim으로. `tests/presentation/test_web_api_wiring.py`.

**검증 제약:** fastapi 미설치 → TestClient 스모크는 이 환경서 SKIP(Task 6 계약테스트 패턴). 대신 (a) 구조적 검증: api.py가 `ui.gradio_app`을 import하지 않음(`_ga()` 제거)을 소스 검사로 확인, (b) 세션 메서드가 endpoint가 기대하는 dict 키를 반환함을 세션 단위테스트로 보장.

- [ ] Step 1: 테스트 — api.py 소스에 `gradio_app`/`_ga(` 없음; 모든 `/api/*` 핸들러가 `session.*()` 호출; fastapi 있으면 `/api/state` 계약(running/tick/units) 유지(importorskip). 실행 → FAIL.
- [ ] Step 2: web_api 로직을 presentation/web/api.py로 이식; `_ga().wargame_start_pause()` 등 → `session.start_pause()` 등으로 교체; `_convert_state_to_api`는 세션 state dict 기반 유지; 엔진은 composition/세션에서 획득(`_wg_ensure_engine` gradio 의존 제거).
- [ ] Step 3: `ui/web_api.py`를 shim(re-export create_app/app from presentation).
- [ ] Step 4~6: 테스트 PASS(fastapi 없으면 관련 SKIP), green+lint, Commit `refactor(presentation): web_api → c2.presentation.web.api + 세션 직접 배선`.

## Task 32: composition root (container.py) + main.py

**Files:** Create `src/c2/composition/container.py`; Modify `main.py`, `src/c2/presentation/web/api.py`(container에서 세션 획득). `tests/composition/test_container.py`.

**Design:** `build_session(agent=None) -> WargameSession` — EventStore factory(`set_default_event_store_factory(lambda: WargameDB())`), 온톨로지 `build_graph_store()`, advisors(`set_planning_advisors(...)` from presentation.tools), agent, 툴 엔진등록 hook(8개 tool `register_wargame_engine`), HarnessStore factory를 한곳에서 wiring. (Slice 3 broad review 체크리스트 반영: harness 팩토리 명명통일.)

- [ ] Step 1: 테스트 — `build_session()`가 완전 wiring된 세션 반환; ensure_engine 후 8개 tool에 엔진 등록됨; advisor 등록됨(tools 존재 시). 실행 → FAIL.
- [ ] Step 2: container 구현; main.py를 container 사용으로; web_api가 container.build_session() 사용.
- [ ] Step 3~5: 테스트 PASS, green+lint, Commit `feat(composition): 조립 루트 container.py + main.py 배선`.

## Task 31: Gradio UI 삭제

**Files:** Delete `ui/gradio_app.py`; Modify `ui/__init__.py`, `main.py`(gradio 서브커맨드 처리), `tests/presentation/test_gradio_removed.py`.

**선행조건 확인:** web_api가 gradio 비의존(Task 30 완료), 오케스트레이션이 세션으로 추출됨(29A~D), main.py의 UI 진입점이 web_api로 전환됨.

- [ ] Step 1: 테스트 — `ui/gradio_app.py` 부재; `grep -rn "gradio_app" --include=*.py`가 (shim/삭제된 참조 제외) 없음; `import ui.web_api` 경로가 gradio 비의존. 실행 → FAIL.
- [ ] Step 2: gradio_app.py 삭제; main.py의 `python main.py ui`를 web_api 기동으로(또는 gradio 옵션 제거); ui/__init__ 정리. 남은 gradio 참조 제거.
- [ ] Step 3~5: 테스트 PASS, 전체 green+lint 3 kept, Commit `refactor(presentation): Gradio UI 삭제 (오케스트레이션은 application으로 추출 완료)`.

---

## Self-Review
- **Spec coverage:** design §3(presentation)·§5 Slice4(T28~32) 매핑. gradio god-object 분해가 원 계획 T29(replan 추출)를 29A~D로 상세화.
- **검증 제약 명시:** fastapi/gradio 부재 → 세션/replan은 런타임 테스트, web_api/gradio는 구조검증 — 각 태스크에 기재.
- **콜백 4종 유지:** 29A/29B에 명시.
- **Slice 5(T33~36):** 이 계획 이후 별도 — shim 전량 제거, 레거시 top-level(wargame/agent/ontology/tools/ui) 삭제, config/scripts/docs/CLAUDE.md 갱신.

## 미해결/이월
- **T12 LLM 백본 포트 갭**: agent가 mission_planner.plan(agent=)로 주입되므로 container가 agent 객체를 넘김 — LLMClient 포트로의 완전 추상화는 향후(agent 런타임이 smolagents/langgraph에 직접 결합).
