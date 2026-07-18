# C2 군사 AI 시스템 — CLAUDE.md

## 프로젝트 개요
EXAONE4 기반 C2(지휘통제) 군사 AI 시스템.
- **워게임 시뮬레이터**: Python 기반 5 vs 5 / 6 vs 6 대대급 시뮬레이터 (SQLite 이벤트 DB)
- **LLM 에이전트**: LangGraph StateGraph(기본) 또는 smolagents CodeAgent(`C2_AGENT_BACKEND` 환경변수로 선택), EXAONE4 단일 모델을 vLLM으로 서빙
- **UI**: FastAPI 웹 API(`c2.presentation.web.api`) + HTML/Leaflet 대시보드(`ui/dashboard/`). **Gradio는 제거됨.**
- **Graph RAG**: COHA 군사 전술 온톨로지(rdflib/TTL)를 이용한 교리 컨텍스트 조회는 유지. ARMA3 연동·PDF RAG·video 관련 기능은 제거됨.

## 아키텍처 개요 — 4계층 클린 아키텍처

코드는 `src/c2/` 아래 4계층으로 구성되며, 의존성 규칙(dependency rule)은 안쪽(domain)만 향한다:

```
c2.presentation  →  c2.infrastructure  →  c2.application  →  c2.domain
```

- **domain**: 순수 값 객체/규칙. 표준 라이브러리만 참조. 상위 계층·레거시 top-level 패키지 import 금지.
- **application**: 유스케이스/오케스트레이션. `c2.domain` + 표준 라이브러리만 참조. `c2.infrastructure`/`c2.presentation`을 직접 import하지 않고 **포트(Protocol)**로 역전 의존한다.
- **infrastructure**: 포트의 구현체(DB, LLM 서빙 클라이언트, 온톨로지 스토어 등).
- **presentation**: smolagents `@tool` 바인딩, LangGraph 에이전트, FastAPI 웹 API.
- **composition**: 조립 루트(composition root). 유일하게 4계층을 전부 자유롭게 import할 수 있는 패키지이며 `.importlinter` 계약 대상에서 제외된다.

**import-linter로 강제**: `.importlinter`에 3개 계약이 정의되어 있고 `PYTHONPATH=src lint-imports` 로 검증한다 (3 kept, 0 broken이 정상).
1. `layers` — `c2.presentation → c2.infrastructure → c2.application → c2.domain` 계층 순서 강제.
2. `domain-no-outward` — `c2.domain`은 `c2.application`/`c2.infrastructure`/`c2.presentation`과 레거시 top-level 패키지(`wargame`/`tools`/`agent`/`ontology`/`ui`/`api`)를 import 금지.
3. `application-no-outward` — `c2.application`은 `c2.infrastructure`/`c2.presentation`과 위 레거시 top-level 패키지를 import 금지.

**레거시 top-level 패키지(`wargame/`, `agent/`, `ontology/`, `tools/`, 구 `ui/gradio_app.py` 등)는 완전히 삭제되었다.** 코드에서 이 경로를 import하면 안 된다.

## 디렉토리 구조

```
src/c2/
  domain/                    # 순수 값 객체/규칙 (표준 라이브러리만 의존)
    wargame/
      unit.py                # Unit 데이터클래스
      combat.py               # 전투 판정 규칙
      coordinates.py          # 위경도 ↔ 미터 좌표 변환
      terrain.py               # 지형 고도·엄폐 계산 (DEM 로딩)
    planning/
      mission_plan.py          # MAP_MAX, Pydantic 스키마(Waypoint/MissionPlanItem/...), validate_mission_plan()
    ontology/
      models.py                 # KnowledgeNode/KnowledgeEdge/Evidence 등 온톨로지 값 객체

  application/                # 유스케이스/오케스트레이션 (domain + 포트만 의존)
    ports/                     # Protocol 인터페이스 (역전 의존의 경계)
      llm.py                    # LLMClient
      event_store.py            # EventStore
      ontology_store.py         # OntologyStore
      conversation_store.py     # ConversationStore
      harness_store.py          # HarnessStore
    simulation/
      engine.py                 # WargameEngine — 틱 루프, 전투/탐지/공중지원 처리
      scenario.py                # 시나리오 초기 배치 (setup_bn_vs_bn 등, _BLUFOR_ZONE/_OPFOR_ZONE)
      session.py                  # WargameSession — 엔진 생명주기/콜백등록/탐지워커 소유
      replan.py                   # 자동 재계획 워커 + 채팅/정찰/공격 플랜 오케스트레이션
    agent/
      mission_planner.py          # MissionPlanner, build_mission_query
    ontology/
      wargame_builder.py, writer.py, retrieval.py, coa_view.py
    harness/                     # 학습/평가 하네스 (controller, episode_runner, rule_manager 등)
    planning/
      mission_session.py          # 의도분류/pending-plan 세션 상태 (domain의 MAP_MAX/validate_mission_plan 참조)

  infrastructure/              # 포트 구현체
    llm/
      vllm_client.py             # vLLM 서빙 공용 클라이언트 (OpenAI 호환 API)
      model_loader.py             # EXAONE4 서빙 클라이언트 로더 (기본 :8000)
      langgraph_llm.py            # LangGraph용 LLM 어댑터
    ontology/
      doctrine_loader.py          # rdflib/TTL 로딩·쿼리 (Graph RAG 순수 로직)
      graph_store.py, in_memory_store.py, factory.py
    persistence/
      sqlite_event_store.py       # WargameDB — SQLite CRUD (DB_PATH)
      harness_db.py                # HarnessDB — 학습 하네스 SQLite CRUD
      conversation_store.py        # 전술채팅 멀티턴 대화 저장 (PostgreSQL/in-memory)

  presentation/                 # 에이전트 바인딩 + 웹 API
    agent/
      battlefield_agent.py         # BattlefieldAgent(smolagents) 래퍼 (intent 분류, 지시사항 주입)
      langgraph_agent.py            # LangGraphBattlefieldAgent (기본 백엔드)
      langgraph_tools.py
    tools/
      wargame_query_tool.py         # get_wargame_situation, get_wargame_battle_log 등
      wargame_mission_tool.py       # apply_wargame_mission_plan, apply_wargame_air_support
      wargame_recon_tool.py         # assess_recon_need, recommend_recon_routes
      wargame_attack_advisor_tool.py    # get_optimal_attack_positions
      wargame_fire_priority_tool.py     # get_fire_priority_schedule
      wargame_opfor_routes_tool.py      # predict_opfor_routes
      wargame_strategy_tool.py          # get_wargame_tactical_recommendation
      coa_analysis_tool.py
      graph_rag_tool.py                  # Graph RAG @tool 래퍼 (doctrine_loader에 위임)
      ontology_query_tool.py
      mission_plan_validator_tool.py
      strategy_advisor_tool.py
    web/
      api.py                        # FastAPI 앱(create_app/start_server) — HTML 대시보드 REST API

  composition/
    container.py                    # build_session() — 전 계층 wiring (조립 루트)

ui/
  dashboard/index.html              # HTML/Leaflet 대시보드 (FastAPI가 정적 서빙)

scripts/
  launch_vllm_servers.py            # vLLM 서버 기동 스크립트 (모델은 별도 프로세스에서 서빙)

config/
  agent_config.yaml                  # CodeAgent 설정 (max_steps 등), 전략 키워드
  models_config.yaml                 # LLM 프로바이더/EXAONE4 서빙 설정
  agent_custom_instructions.txt      # [ATTACK] [RECON] [EXECUTION] [LEARNED_RULES] 섹션
  tactical_memory.json               # 전술 학습 메모리(zone 페널티/보너스)

main.py                              # 진입점 (ui / query / check-env 커맨드)
```

## 핵심 상수

| 항목 | 값 | 위치 |
|------|-----|------|
| 맵 크기 | 30,000 × 30,000 m | `c2.domain.planning.mission_plan:MAP_MAX` |
| 좌표 단위 | 미터(m) 정수 | 전체 공통 |
| 기본 배속 | 60 (실제 1초 = 게임 60초) | `c2.application.simulation.engine:WargameEngine.time_scale` |
| 틱 간격 | 0.5초 (2Hz) | `c2.application.simulation.engine:WargameEngine.tick_interval` |
| CP 임계값 트리거 | 70%, 30% | `c2.application.simulation.engine:_CP_THRESHOLDS` |
| OPFOR 공중지원 쿨다운 | 900 게임초(15분) | `c2.application.simulation.engine:_OPFOR_AIR_INTERVAL` |
| 피격 판정 윈도우 | 5틱 | `c2.presentation.tools.wargame_query_tool:_ATTACK_WINDOW_TICKS` |
| BLUFOR 배치 구역 | x 5000~10000, y 5000~10000 (대대 정면 ~5km) | `c2.application.simulation.scenario:_BLUFOR_ZONE` |
| OPFOR 배치 구역 | x 18000~23000, y 18000~23000 (대대 정면 ~5km) | `c2.application.simulation.scenario:_OPFOR_ZONE` |
| 이벤트 DB 경로 | `data/wargame_state.db` | `c2.infrastructure.persistence.sqlite_event_store:DB_PATH` |
| 지형 DEM 데이터 | `data/korea_dem_cheorwon.npy` 등 | `c2.domain.wargame.terrain:DATA_DIR/DEM_FILE/META_FILE` |

## 자동 재계획 이벤트 시스템

네 가지 이벤트가 `WargameSession.detection_queue`로 들어가고 `c2.application.simulation.replan.detection_worker()` 스레드가 처리한다.

```
("detection",    enemy_id, unit_type, x, y)             # 신규 OPFOR 탐지
("cp_threshold", unit_id, unit_type, threshold, cp)      # BLUFOR CP 70%/30% 이하
("air_hit",      unit_id, unit_type, call_sign, cp)      # BLUFOR OPFOR 공중지원 피격
("target_moved", unit_id, unit_type, target_id, dist_m)  # 담당 표적 1km+ 이동 (접근 중)
```

**콜백 등록 규칙**: `WargameSession._register_callbacks()`가 `ensure_engine()`과 `reset()` 양쪽에서 항상 네 콜백을 (재)등록한다.
새 이벤트 유형 추가 시 → `WargameSession._register_callbacks()` (그리고 `enqueue_*` 메서드)에도 추가 필요.

## WargameEngine 주요 메서드 (`c2.application.simulation.engine`)

```python
engine.start() / engine.stop()          # 시뮬레이션 시작/정지
engine.reset(units)                      # 상태 초기화 (콜백은 유지됨)
engine.get_state() -> dict               # 현재 전장 상태 스냅샷
engine.apply_mission_plan(plan: dict)    # BLUFOR 임무 적용 (plan에 있는 부대만 업데이트)
engine.apply_air_support_plan(plan)      # BLUFOR 공중지원 등록
engine.get_intelligence_report(side)     # 탐지 인텔 보고서

# 콜백 (WargameSession이 등록)
engine.on_new_opfor_detection: Callable  # (enemy_id, unit_type, x, y)
engine.on_blufor_cp_threshold: Callable  # (unit_id, unit_type, threshold_pct, current_cp)
engine.on_blufor_air_hit: Callable       # (unit_id, unit_type, call_sign, current_cp)
engine.on_target_moved: Callable         # (unit_id, unit_type, target_id, moved_dist_m)
```

## 세션·DI·조립 루트 (WargameSession + composition root)

- **`c2.application.simulation.session.WargameSession`**: 워게임 세션의 엔진 생명주기(생성/콜백등록/리셋/시작정지/배속/자동재계획 상태)를 소유하는 애플리케이션 객체. `c2.domain`/`c2.application`/표준 라이브러리만 import하며, `c2.infrastructure`/`c2.presentation`(tools/agent/web)은 생성자 주입(`engine_factory`, `tool_register_hook`, `graph_store_factory`, `ontology_writer_factory`, `replan_hooks`, `agent`)으로만 접근한다.
- **`c2.application.simulation.replan`**: 자동 재계획 워커(`detection_worker`)와 `request_recon_plan()`/`request_attack_plan()`/`chat_send()`/`evaluate_and_learn()`/`execute_auto_attack_plan()` 오케스트레이션을 담당. `WargameSession`의 동일 이름 메서드들이 이 모듈에 위임한다.
- **`c2.composition.container.build_session(agent=None) -> WargameSession`**: 조립 루트(composition root). 전 계층을 자유롭게 import할 수 있는 유일한 패키지(`.importlinter` 계약 대상 아님). 다음을 wiring한다:
  1. EventStore 기본 팩토리 — `set_default_event_store_factory(lambda: WargameDB())`
  2. presentation 8개 툴에 엔진 등록 (`tool_register_hook`)
  3. 온톨로지 그래프 스토어 팩토리 (`build_graph_store()` + 조회 툴 등록)
  4. OntologyWriter 팩토리
  5. `replan_hooks`(`_ContainerReplanHooks`) — 자동 재계획 워커 ↔ presentation 툴(apply tracker, 학습규칙 조회 등) 연동
  6. 계획 자문(advisor) 등록 — `set_planning_advisors(recon=, attack=, fire=)`
  7. HarnessStore 기본 팩토리 — `set_default_harness_db_factory(lambda: HarnessDB())`
  8. 주입된 `agent`
- **포트**: `c2.application.ports`에 `LLMClient`/`EventStore`/`OntologyStore`/`ConversationStore`/`HarnessStore` Protocol이 정의되어 있다. `c2.infrastructure`의 구현체(`WargameDB`, `HarnessDB`, `Neo4jGraphStore`/`InMemoryGraphStore`, `VLLMServerClient` 등)가 이를 만족한다.
- `main.py`의 `cmd_ui()`가 `build_session(agent=agent)`를 호출한 뒤 `c2.presentation.web.api.start_server()`를 실행한다.

## BLUFOR 표적 추격 (target_unit_id)

- 임무계획의 `mission_plans[].target_unit_id`로 각 공격부대(attack/flank)가 담당할 적 부대 지정
- `engine.apply_mission_plan()`에서 부대에 `target_unit_id` 저장 + 발령 시점 표적 인지 위치를 `target_ref_x/y`에 스냅샷
- **경유지(waypoints) 완주 후** `_on_waypoints_empty()`가 표적의 현재 인지 위치로 지속 추격 (`_PURSUE_REACQUIRE_M`=60m 이상 이동 시 재기동), `u.pursuing=True`
- 추격 중에는 `_blufor_llm_units` 유지 + `pursuing` 플래그로 룰 AI 개입 차단
- 표적이 격멸/탐지상실(lost)되면 추격 종료 → hold 전환
- **접근 중(추격 전, waypoints 남음)** 표적이 `_TARGET_MOVE_REPLAN_M`=1000m 이상 이동하면 `_check_target_moved()`가 `on_target_moved` 콜백을 부대별 1회 발동 → LLM 공격 재계획
- 표적 위치는 아군 인텔(`detected`/`approximate`)의 `known_x/known_y` 기준 (FOW 준수)

## BLUFOR LLM 임무 잠금

- `engine.apply_mission_plan()` 호출 시 해당 부대 `mission_lock_ticks = 30` 설정
- 30틱 동안 룰 기반 AI 개입 차단
- **잠금 해제 후에도** `_blufor_llm_units`에 있고 `waypoints`가 남아 있으면 AI 개입 차단 (경로 덮어쓰기 방지)
- 모든 waypoint 완주 시 `_blufor_llm_units`에서 제거

## BLUFOR 은밀 기동 경로 확장

- `engine.apply_mission_plan()`에서 BLUFOR 부대의 LLM waypoint를 `_stealth_expand_waypoints()`로 확장
- LLM이 준 **목표 지점(원본 WP)은 항상 유지**하고, 각 구간(현위치→A, A→B, …)만 발각 위험이 낮은 우회 경로로 치환
- 발각 위험 = 엔진 탐지 모델과 동일 요소: 적과의 거리 / LOS 차폐(`_los_quality`) / 지형 엄폐(`cover_factor`, `c2.domain.wargame.terrain`)
- 위협원 = 아군 인텔의 OPFOR(`detected`/`approximate`) — 적 정찰(`_DETECT_RANGE` 8km)이 자동으로 넓게 회피됨
- 인지된 적이 없으면 원본 waypoint 그대로 사용, OPFOR·룰기반 이동에는 미적용
- 파라미터: `c2.application.simulation.engine:_STEALTH_*` (샘플 간격, 우회 후보 크기, 재귀 깊이 등)

## 공중지원 처리 주의사항

- `pending → active` 전환 시 초과 시간을 `eff_dt = elapsed - delay`로 carry-over
- `eff_dt_h`를 피해 계산에 사용 (고배속에서 피해 누락 방지)
- 자동 재계획 트리거 시 `pending/active` 공중지원이 모두 완료될 때까지 대기 후 정지 (최대 120초)

## Mission Plan JSON 형식

```json
{
  "reasoning": "한국어 판단 근거",
  "mission_plans": [
    {
      "company_id": "Alpha",
      "mission_type": "attack|defend|flank|withdraw|hold|recon",
      "target_unit_id": "Red1",
      "waypoints": [[x_m, y_m], ...],
      "objective": "임무 목표 설명"
    }
  ],
  "air_support_plans": [
    {
      "call_sign": "EAGLE-1",
      "support_type": "cas|strike|artillery|helicopter",
      "target": [x_m, y_m],
      "radius": 1500,
      "delay": 60
    }
  ]
}
```

스키마 검증은 `c2.domain.planning.mission_plan`(Pydantic: Waypoint/MissionPlanItem/AirSupportItem/MissionPlanRequest, `validate_mission_plan()`)에서 수행한다.

- `waypoints`는 `[x, y]` 리스트 또는 `{"x": x, "y": y}` 딕셔너리 모두 허용 (validator에서 자동 변환)
- `target_unit_id` (선택): attack/flank 부대가 담당·추격할 적 부대 ID. 경유지 완주 후 이 표적을 지속 추격
- `target` 좌표는 반드시 `get_wargame_situation()`에서 조회한 탐지된 OPFOR 실제 좌표 사용
- `engine.apply_mission_plan()`은 `mission_plans`에 포함된 부대만 업데이트 (선택적 재배정)

## 에이전트 실행 경로

| 경로 | 함수 | 특징 |
|------|------|------|
| 자동 재계획 | `c2.application.simulation.replan:execute_auto_attack_plan()` | `WargameSession`의 탐지 워커 스레드, 900초 타임아웃, `reset=True` |
| 수동 공격 요청 | `c2.application.simulation.replan:request_attack_plan()` (`WargameSession.request_attack_plan()` 경유) | 웹 API `/api/mission/attack`, `reset=True` |
| 수동 정찰 요청 | `c2.application.simulation.replan:request_recon_plan()` (`WargameSession.request_recon_plan()` 경유) | 웹 API `/api/mission/recon`, `reset=False` |
| 채팅 | `c2.application.simulation.replan:chat_send()` (`WargameSession.chat_send()` 경유) | 웹 API `/api/chat` |

에이전트 결과 처리 우선순위:
1. `raw`에 `"mission_plans"` 있음 → 직접 적용
2. `raw`에 `{"status": "success"}` 있음 → 이미 툴로 적용 완료, 스킵
3. 그 외 → 규칙 기반 폴백

## 전술 규칙 학습

- `config/agent_custom_instructions.txt`의 `[LEARNED_RULES]` 섹션에 누적
- `append_learned_rule(rule)` 로 추가 (`c2.presentation.agent.battlefield_agent`)
- 규칙은 좌표·부대ID 없는 범용 형태로 작성 (특정 전투 상황 언급 금지)

## Graph RAG (교리 온톨로지)

- COHA 군사 전술 온톨로지(OWL/Turtle, rdflib)를 이용한 교리 컨텍스트 조회 기능. ARMA3 연동·PDF RAG·video 처리 기능과 달리 **유지됨**.
- 순수 rdflib 로딩/쿼리 로직: `c2.infrastructure.ontology.doctrine_loader` (인프라 계층)
- `@tool` 바인딩(얇은 래퍼): `c2.presentation.tools.graph_rag_tool`
- 사용처: `recommend_recon_routes()`(정찰 ISR·지형 교리), `get_optimal_attack_positions()`(공격 기동·화력 교리), LLM 에이전트의 임의 전술 개념 조회

## 금지 사항

- `WargameSession._register_callbacks()` 수정 시 콜백 4종 재등록 블록 반드시 유지 (`ensure_engine()`/`reset()` 양쪽에서 호출됨)
- `waypoints` 좌표는 반드시 미터(m) 정수 (9000 O, 9 X)
- 에이전트 자동 재계획 쿼리에서 `recommend_recon_routes` 호출 금지
- `engine.apply_mission_plan()` 이중 호출 금지 (툴로 적용 완료 후 재적용 X)
- `c2.application`은 `c2.infrastructure`/`c2.presentation`(및 레거시 top-level 패키지)을 직접 import 금지 — 필요한 경우 포트(`c2.application.ports`) + 생성자 주입으로 역전 의존할 것. `c2.domain`도 동일하게 상위 계층 import 금지 (import-linter `domain-no-outward`/`application-no-outward` 계약이 강제, `PYTHONPATH=src lint-imports`로 검증)
- 레거시 top-level 패키지(`wargame`/`agent`/`tools`/`ontology`/구`ui/gradio_app.py`)는 삭제되었으므로 새 코드에서 참조 금지 — 항상 `c2.*` 경로 사용
