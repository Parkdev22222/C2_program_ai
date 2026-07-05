# C2 군사 AI 시스템 — CLAUDE.md

## 프로젝트 개요
EXAONE4 기반 C2(지휘통제) 군사 AI 시스템.
- **워게임 시뮬레이터**: Python 기반 5 vs 5 대대급 시뮬레이터 (SQLite 이벤트 DB)
- **LLM 에이전트**: smolagents CodeAgent (EXAONE4 단일 모델, vLLM 서빙)
- **UI**: Gradio + Plotly 실시간 전장 지도

## 디렉토리 구조

```
wargame/          # 워게임 엔진 코어
  engine.py       # WargameEngine — 틱 루프, 전투/탐지/공중지원 처리
  models.py       # Unit, AirSupport 데이터클래스
  scenario.py     # 시나리오 초기 배치 (setup_bn_vs_bn, setup_bn_vs_bn_blufor_random)
  llm_planner.py  # MissionPlanner, build_mission_query
  terrain.py      # 지형 고도·엄폐 계산

agent/
  battlefield_agent.py   # BattlefieldAgent 래퍼 (intent 분류, 지시사항 주입)
  vllm_client.py         # vLLM 서빙 공용 클라이언트 (OpenAI 호환 API)
  model_loader.py        # EXAONE4 서빙 클라이언트 로더 (기본 :8000)

scripts/
  launch_vllm_servers.py # vLLM 서버 기동 스크립트 (모델은 별도 프로세스에서 서빙)

tools/
  wargame_query_tool.py       # get_wargame_situation, get_wargame_battle_log 등
  wargame_mission_tool.py     # apply_wargame_mission_plan, apply_wargame_air_support
  wargame_recon_tool.py       # assess_recon_need, recommend_recon_routes
  wargame_attack_advisor_tool.py  # get_optimal_attack_positions
  wargame_opfor_routes_tool.py    # predict_opfor_routes
  wargame_strategy_tool.py        # get_wargame_tactical_recommendation
  mission_plan_validator.py       # Pydantic 스키마 검증 (MissionPlanRequest)
  strategy_advisor_tool.py        # 상황 분석 세션 메모리 (자문 툴은 제거됨)

ui/
  gradio_app.py   # Gradio UI + 자동 재계획 워커 스레드

config/
  agent_config.yaml              # CodeAgent 설정 (max_steps: 30)
  agent_custom_instructions.txt  # [ATTACK] [RECON] [EXECUTION] [LEARNED_RULES] 섹션
```

## 핵심 상수

| 항목 | 값 | 위치 |
|------|-----|------|
| 맵 크기 | 30,000 × 30,000 m | `mission_plan_validator.py:MAP_MAX` |
| 좌표 단위 | 미터(m) 정수 | 전체 공통 |
| 기본 배속 | 60 (실제 1초 = 게임 60초) | `engine.py:time_scale` |
| 틱 간격 | 0.5초 (2Hz) | `engine.py:tick_interval` |
| CP 임계값 트리거 | 70%, 30% | `engine.py:_CP_THRESHOLDS` |
| OPFOR 공중지원 쿨다운 | 900 게임초(15분) | `engine.py:_OPFOR_AIR_INTERVAL` |
| 피격 판정 윈도우 | 5틱 | `wargame_query_tool.py:_ATTACK_WINDOW_TICKS` |
| BLUFOR 배치 구역 | x 2000~13000, y 1500~12000 | `scenario.py:_BLUFOR_ZONE` |
| OPFOR 배치 구역 | x 17000~28000, y 17000~28500 | `scenario.py:_OPFOR_ZONE` |

## 자동 재계획 이벤트 시스템

세 가지 이벤트가 `_detection_queue`로 들어가고 `_detection_worker` 스레드가 처리한다.

```
("detection",    enemy_id, unit_type, x, y)         # 신규 OPFOR 탐지
("cp_threshold", unit_id, unit_type, threshold, cp)  # BLUFOR CP 70%/30% 이하
("air_hit",      unit_id, unit_type, call_sign, cp)  # BLUFOR OPFOR 공중지원 피격
```

**콜백 등록 규칙**: `wargame_reset_sim()` 에서 항상 세 콜백을 재등록한다.
새 이벤트 유형 추가 시 → `wargame_reset_sim()` 의 콜백 등록 블록에도 추가 필요.

## WargameEngine 주요 메서드

```python
engine.start() / engine.stop()          # 시뮬레이션 시작/정지
engine.reset(units)                      # 상태 초기화 (콜백은 유지됨)
engine.get_state() -> dict               # 현재 전장 상태 스냅샷
engine.apply_mission_plan(plan: dict)    # BLUFOR 임무 적용 (plan에 있는 부대만 업데이트)
engine.apply_air_support_plan(plan)      # BLUFOR 공중지원 등록
engine.get_intelligence_report(side)     # 탐지 인텔 보고서

# 콜백 (외부에서 등록)
engine.on_new_opfor_detection: Callable  # (enemy_id, unit_type, x, y)
engine.on_blufor_cp_threshold: Callable  # (unit_id, unit_type, threshold_pct, current_cp)
engine.on_blufor_air_hit: Callable       # (unit_id, unit_type, call_sign, current_cp)
```

## BLUFOR LLM 임무 잠금

- `apply_mission_plan()` 호출 시 해당 부대 `mission_lock_ticks = 30` 설정
- 30틱 동안 룰 기반 AI 개입 차단
- **잠금 해제 후에도** `_blufor_llm_units`에 있고 `waypoints`가 남아 있으면 AI 개입 차단 (경로 덮어쓰기 방지)
- 모든 waypoint 완주 시 `_blufor_llm_units`에서 제거

## BLUFOR 은밀 기동 경로 확장

- `apply_mission_plan()`에서 BLUFOR 부대의 LLM waypoint를 `_stealth_expand_waypoints()`로 확장
- LLM이 준 **목표 지점(원본 WP)은 항상 유지**하고, 각 구간(현위치→A, A→B, …)만 발각 위험이 낮은 우회 경로로 치환
- 발각 위험 = 엔진 탐지 모델과 동일 요소: 적과의 거리 / LOS 차폐(`_los_quality`) / 지형 엄폐(`cover_factor`)
- 위협원 = 아군 인텔의 OPFOR(`detected`/`approximate`) — 적 정찰(`_DETECT_RANGE` 8km)이 자동으로 넓게 회피됨
- 인지된 적이 없으면 원본 waypoint 그대로 사용, OPFOR·룰기반 이동에는 미적용
- 파라미터: `engine.py:_STEALTH_*` (샘플 간격, 우회 후보 크기, 재귀 깊이 등)

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

- `waypoints`는 `[x, y]` 리스트 또는 `{"x": x, "y": y}` 딕셔너리 모두 허용 (validator에서 자동 변환)
- `target` 좌표는 반드시 `get_wargame_situation()`에서 조회한 탐지된 OPFOR 실제 좌표 사용
- `apply_mission_plan()`은 `mission_plans`에 포함된 부대만 업데이트 (선택적 재배정)

## 에이전트 실행 경로

| 경로 | 함수 | 특징 |
|------|------|------|
| 자동 재계획 | `_execute_auto_attack_plan()` | 백그라운드 스레드, 900초 타임아웃, `reset=True` |
| 수동 공격 버튼 | `run_attack_mission()` | UI 스레드, `reset=True` |
| 수동 정찰 버튼 | `wargame_request_recon_plan()` | UI 스레드, `reset=False` |
| 채팅 | `chat_with_agent()` | UI 스레드 |

에이전트 결과 처리 우선순위:
1. `raw`에 `"mission_plans"` 있음 → 직접 적용
2. `raw`에 `{"status": "success"}` 있음 → 이미 툴로 적용 완료, 스킵
3. 그 외 → 규칙 기반 폴백

## 전술 규칙 학습

- `agent_custom_instructions.txt`의 `[LEARNED_RULES]` 섹션에 누적
- `append_learned_rule(rule)` 로 추가 (`agent/battlefield_agent.py`)
- 규칙은 좌표·부대ID 없는 범용 형태로 작성 (특정 전투 상황 언급 금지)

## 금지 사항

- `wargame_reset_sim()` 수정 시 콜백 재등록 블록 반드시 유지
- `waypoints` 좌표는 반드시 미터(m) 정수 (9000 O, 9 X)
- 에이전트 자동 재계획 쿼리에서 `recommend_recon_routes` 호출 금지
- `apply_mission_plan()` 이중 호출 금지 (툴로 적용 완료 후 UI에서 재적용 X)
- `_wg_ensure_engine()` 대신 `wargame_reset_sim()` 에서 콜백을 관리하므로 콜백 등록 로직을 `_wg_ensure_engine()`에 추가하지 말 것
