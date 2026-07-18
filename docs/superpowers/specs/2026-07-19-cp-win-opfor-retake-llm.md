# 통제구역 승리조건 500틱 + OPFOR 탈환 + LLM 통제구역 정보 설계

- 날짜: 2026-07-19
- 대상: `c2.application.simulation.engine`, `c2.application.agent.mission_planner`, 특성화 골든
- import-linter 계약 영향 없음

## 1. 목표
1. 통제구역 다수(≥2곳) **500틱** 연속 확보 또는 **적 전멸** 시 승리.
2. BLUFOR가 통제구역을 확보하면 OPFOR가 **전 부대 총력으로 탈환** 공격.
3. LLM 공격 임무계획 시 **확보할 통제구역 좌표·반경·소유 정보**를 프롬프트로 제공.

## 2. A. 승리조건 — 다수(≥2곳) 500틱 (전멸 병행)
- 상수: `_CP_HOLD_TO_WIN`(300.0 게임초) 제거 → **`_CP_HOLD_TO_WIN_TICKS = 500`**.
- `_cp_majority_since`(side→값)를 game_time 대신 **tick** 저장으로 변경(값은 int tick 또는 None).
- `_update_control_points`:
  - 각 side가 CP ≥2곳 소유 시 `_cp_majority_since[side]`가 None이면 `self.tick` 기록; 이미 있으면 `self.tick - since >= _CP_HOLD_TO_WIN_TICKS` 이면 `_cp_winner = side`.
  - <2곳이면 None으로 리셋.
- `_check_winner`: 전멸 판정 우선 → `_cp_winner` (기존 구조 유지).
- `get_state`의 `control_points` 각 항목에 **`radius` 필드 추가** = `_CP_CAPTURE_RADIUS`(2000.0). (UI·LLM 쿼리 공용)
- 기존 테스트 `tests/application/test_control_points.py`가 `_CP_HOLD_TO_WIN`을 참조하므로 `_CP_HOLD_TO_WIN_TICKS`로 갱신하고 `+3틱` 여유로 실행.

## 3. B. OPFOR 탈환 — 전 부대 총력
- 상태: `_opfor_retaking: bool`(기본 False) — `__init__`/`reset()`에서 초기화.
- 신규 `_opfor_retake_strategy(active_opfor, held_positions)`:
  - **기동부대(비자주포) 전원**: 가장 가까운 BLUFOR-확보 CP 좌표로 `waypoints=[[tx,ty]]`, `current_action="attack"`. 이미 반경 절반 이내면 waypoints 비우고 attack(경합 유지).
  - **자주포**: 확보 CP들 중심 방향으로 `_ai_standoff(u, cx, cy, "OPFOR_AI")` 화력지원.
- Hook — `_run_opfor_strategy_ai`에서 `active_opfor` 확정 직후(정찰/결정 앞):
  - `blufor_held = [(cp.x, cp.y) for cp in self._control_points if self._cp_owner.get(cp.id)=="BLUFOR"]`
  - `blufor_held`가 있으면: 최초 진입 시(`not self._opfor_retaking`) 로그 `OPFOR_AI` "탈환 공격 개시 — 확보 통제구역 N곳" + `_opfor_retaking=True`; `_opfor_retake_strategy(active_opfor, blufor_held)` 호출 후 `return`(방어/공격 override).
  - 없으면 `_opfor_retaking=False`로 리셋(기존 전략 복귀). `_opfor_strategy`는 건드리지 않음(복귀 시 정상 동작 보존).
- 주기: OPFOR AI 60게임초마다 재평가. BLUFOR가 CP를 다 내주면 자동 복귀.

## 4. C. LLM 공격계획에 통제구역 정보
- `mission_planner.py`에 헬퍼 `_build_control_point_block(state) -> str`:
  - `state.get("control_points", [])` 각 항목을 한 줄로: `{id}: 좌표[x_m, y_m] 반경{radius}m 현재소유={owner|중립} (아군{blufor_near}/적{opfor_near})`.
  - 헤더: 승리조건(≥2곳 500틱 or 전멸) 명시 + "공격부대 waypoint를 통제구역 좌표로 지향해 ≥2곳 확보·유지" 지시.
  - control_points 없으면 빈 문자열.
- `build_mission_query`가 이 블록을 만들어:
  - **smolagents 분기**: `[제공 데이터]`의 `{attack_pos_block}` 뒤에 `{cp_block}` 삽입.
  - **langgraph 분기**: `_build_mission_query_funccall`에 `cp_block` 인자 추가 → `[제공 데이터]` 영역에 삽입.
- 좌표는 미터(state control_points는 엔진 좌표) — 임무계획 waypoint와 동일 단위.

## 5. D. 골든 재생성 (필수)
- 승리 타이머(틱화)·OPFOR 탈환으로 900틱 궤적 변화(BLUFOR가 CP 구역 통과·확보 시 탈환 발동) → `engine_900tick_seed42.json` 재생성. 결정성(a==b) 유지, 교전 발생 확인.

## 6. 테스트 계획
- A: `_CP_HOLD_TO_WIN_TICKS` 존재; 2부대로 ≥2 CP를 500틱 유지 시 winner=BLUFOR; get_state control_points에 radius 포함.
- B: BLUFOR 1부대를 CP 위에 두고 OPFOR AI 실행 → OPFOR 부대가 그 CP로 향하는 waypoint/attack; `_opfor_retaking` True; BLUFOR 이탈 시 리셋.
- C: `_build_control_point_block(state)`가 CP id·좌표·반경·"확보" 문자열 포함; control_points 없으면 빈 문자열.
- 골든: 재생성 후 determinism green + 교전 발생.

## 7. 미포함(향후)
- 자주포까지 CP 돌격, CP별 부분점령 진행바, 탈환 시 은밀기동 경로.
