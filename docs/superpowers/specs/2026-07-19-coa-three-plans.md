# 공격 임무계획 3-COA 생성 + 지도 프리뷰 + 클릭 실행 + 채팅 수정 설계

- 날짜: 2026-07-19
- 대상: `c2.application.simulation`(replan/session), `c2.application.agent.mission_planner`, `c2.presentation.web.api`, `ui/dashboard/index.html`
- 사용자 위임(취침): 권장 방식으로 전부 구현. 아침 리뷰용으로 설계 결정을 문서화.

## 1. 목표 (사용자 요청)
1. **공격 임무계획 버튼** → 즉시 실행이 아니라 **3개 COA(COA1/2/3) 생성**(엔진 미적용).
2. 전술채팅 영역에 **COA1~3 버튼** 표시(첨부 이미지처럼).
3. COA 버튼 **hover** → 그 COA의 각 부대 기동 경로·공중지원·포격을 **지도에 프리뷰**, 버튼에서 **벗어나면 원래 지도로 복귀**. COA1~3 모두.
4. COA 버튼 **클릭** → 해당 COA를 **실행**(엔진 적용). 클릭해야만 실행.
5. 생성된 COA를 수정하고 싶으면 **전술채팅으로 수정 쿼리** 전송.

## 2. 설계 원칙 / 결정 (아침 리뷰용)
- **신뢰 백본 = 규칙기반 3-COA 생성**: LLM(vLLM/Gemini) 없이도 항상 3개 **구별되는** COA를 결정적으로 생성. LLM 사용 가능 시 각 COA를 LLM으로 대체(향상), 실패 시 규칙기반 폴백. → 데모/테스트가 모델 가용성과 무관하게 동작.
- **3개 COA 교리(doctrine)**:
  - COA1 = **정면 집중** (통제-브라보 중앙 확보 우선, 기동부대 밀집 진격)
  - COA2 = **측방 기동** (통제-알파/찰리 측면 확보, 우회 waypoint)
  - COA3 = **화력 우선** (공중지원·포병 최대 투사 후 진격)
- **생성 시 엔진 미적용**: 3개 COA는 `session.pending_coas`에 저장만. 클릭(실행) 시에만 `engine.apply_mission_plan`.
- **프리뷰 = 계획의 waypoint/공중지원 원본**(미터→위경도 변환). 실행 시 엔진의 은밀기동 확장은 프리뷰에 반영하지 않음(경미한 차이, 문서화).

## 3. 백엔드

### 3-1. 세션 상태 (`WargameSession`)
- `self._pending_coas: list = []` (생성/reset에서 초기화).
- `set_pending_coas(coas: list)`, `pending_coas` (property/getter), `clear_pending_coas()`. `reset()`에서 clear.

### 3-2. 프리뷰 빌더 (순수 함수, `replan.py`)
- `build_coa_preview(plan: dict, state: dict) -> dict`:
  - `routes`: `[{"unit_id", "color", "latlon": [[lat,lon], ...]}]` — 각 mission_plan의 부대 현재 위치(state units에서 조회) + waypoints(미터→위경도).
  - `air_support`: `[{"call_sign","support_type","target":[lat,lon],"radius"}]`.
  - 좌표 변환은 `c2.domain.wargame.coordinates.xy_to_latlon` 사용.
- 순수·결정적 → 단위 테스트.

### 3-3. 규칙기반 3-COA 생성 (`mission_planner.py`)
- `build_rule_based_coas(state: dict) -> list[dict]`:
  - 교리별로 mission_plans(+air_support_plans) 구성. 탐지 OPFOR·통제구역·`get_optimal_attack_positions`(advisor) 데이터 활용.
  - 각 COA: `{"id":"COA1","label":"COA1 · 정면 집중","doctrine":"frontal","plan":{...},"summary":"..."}`.
  - 3개는 서로 다른 waypoint 목표(중앙/측방)·공중지원 강도로 **구별**되게.
- 테스트: 3개 반환, 서로 다른 plan, 각 plan이 `validate_mission_plan` 통과.

### 3-4. COA 생성 오케스트레이션 (`replan.py`, 백그라운드 잡)
- `generate_attack_coas(session) -> dict`:
  1. `coas = build_rule_based_coas(state)` (백본).
  2. 에이전트 있으면 각 COA를 LLM으로 대체 시도: `build_mission_query(state)` + 교리 힌트 + "**출력만, 엔진 적용 금지**" → `agent.agent.run(reset=True)` → `_parse_json` → 유효하면 그 COA의 plan 교체(**미적용**). 실패/빈값이면 규칙기반 유지.
  3. 각 COA에 `preview = build_coa_preview(plan, state)` 부착.
  4. `session.set_pending_coas(coas)`.
  5. 반환 `{"coas": [{id,label,doctrine,summary,preview,plan}...], "history":[...]}`.
  - **엔진 미적용**(즉시 실행 안 함). 시뮬은 일시정지만(기존 공격계획과 동일 UX).

### 3-5. COA 실행 (`replan.py`)
- `execute_coa(session, index: int) -> dict`:
  - `plan = session.pending_coas[index]["plan"]`; `engine.apply_mission_plan(plan)` + `engine.apply_air_support_plan(plan)`(있으면).
  - 시뮬 재개, `session.clear_pending_coas()`, 반환 `{"ok":True,"executed":"COA{index+1}"}`.
  - 인덱스 범위 밖/COA 없음 → `{"ok":False,"error":...}`.
- 테스트: pending_coas 세팅 후 execute → 엔진 부대 waypoints/current_action 갱신 확인.

### 3-6. 채팅 COA 수정 (`replan.py` `chat_send` 확장)
- `session.pending_coas`가 있으면 채팅 컨텍스트에 현재 COA 요약+plan 주입 + 지시: "사용자가 특정 COA(1/2/3) 수정 요청 시, 수정된 mission_plans JSON을 반환하고 어느 COA인지 명시."
- 에이전트 응답에 mission_plans JSON이 있으면: 대상 COA(명시 없으면 COA1) plan 교체 → preview 재생성 → `session.set_pending_coas`.
- 반환 dict에 `"coas": [...]` 포함(변경 시) → 프론트가 버튼/프리뷰 갱신.
- LLM 의존(모델 가용 시 동작) — 문서화. 미적용은 유지(수정만, 실행은 클릭).

### 3-7. API (`api.py`)
- `POST /api/mission/attack` **변경**: 잡이 `generate_attack_coas` 실행 → 결과 `{"coas":[...]}`(위경도 프리뷰 포함, plan 포함). (즉시 실행 안 함)
- `POST /api/mission/coa/execute` (body `{"index":int}`) → `execute_coa`. 반환 `{"ok",...}`.
- `POST /api/chat` **변경**: 응답에 `coas`가 있으면 그대로 전달.
- 프리뷰 좌표는 백엔드에서 위경도 변환 완료 상태로 전달(프론트는 그대로 렌더).

## 4. 프론트엔드 (`ui/dashboard/index.html`)
- `startMission('attack')` → 잡 완료 시 `result.coas` 있으면 `renderCoaButtons(coas)` (채팅 영역에 COA1~3 버튼, 이미지 스타일).
- 각 COA 버튼:
  - `mouseenter` → `renderCoaPreview(coa.preview)`: waypoint 폴리라인 + 공중지원 원(전용 레이어 `coaPreviewLayers`, live 레이어와 분리, 앰버/점선 강조).
  - `mouseleave` → `clearCoaPreview()` (프리뷰 레이어만 제거, 실시간 지도 복귀).
  - `click` → `POST /api/mission/coa/execute {index}` → 성공 시 프리뷰·버튼 제거, 확인 메시지, `poll()` 갱신.
- `sendChat` → `/api/chat` 응답에 `coas` 있으면 `renderCoaButtons(coas)` 재호출(수정 반영).
- 프리뷰 레이어는 `unitWpLines`/`airCircles`(실시간)와 별개 캐시로 관리 → 충돌·잔류 없음.

## 5. 테스트 계획
- 세션 pending_coas 상태(set/get/clear/reset).
- `build_coa_preview`: plan → 위경도 routes/air 변환(순수).
- `build_rule_based_coas`: 3개 구별 COA + validate 통과.
- `execute_coa`: 적용 후 엔진 부대 갱신 + 범위밖 에러.
- API 계약: attack 잡 결과에 coas / coa/execute / chat 응답 coas 전달(규칙기반·스텁 경로로 결정적 검증).
- 대시보드: renderCoaButtons/renderCoaPreview/clearCoaPreview/coaPreviewLayers 문자열 스모크.
- 회귀: 전체 스위트 + import-linter 3 kept.

## 6. 알려진 한계 / 아침 리뷰 포인트
- **LLM COA 대체·채팅 수정**은 vLLM/Gemini 가용 시에만 동작(현재 환경엔 미기동). 규칙기반 백본은 항상 동작.
- 프리뷰 waypoint는 계획 원본(은밀기동 확장 전) — 실행 후 실제 경로와 경미한 차이 가능.
- 규칙기반 3-COA 교리 구현의 전술 품질은 1차 버전(추후 정교화 여지).

## 7. 미포함(향후)
- COA별 예상 피해/성공확률 스코어링, 프리뷰에 포병 착탄 애니메이션, COA 저장/비교 히스토리.
