# 이벤트 자동 재계획 COA 버튼화 + 프리뷰/실행 경로 완전 일치 설계

- 날짜: 2026-07-20
- 대상: `c2.application.simulation`(engine/replan/session), `c2.presentation.web.api`, `ui/dashboard/index.html`
- 선행: 공격 3-COA 인프라(2026-07-19-coa-three-plans)

## 1. 목표 (사용자 요청)
- **A. 이벤트 자동 재계획도 COA 버튼 방식**: 탐지/CP저하/공중피격/표적이동 이벤트로 자동 재계획이 트리거될 때, 즉시 적용 대신 **COA1/2/3 생성 → 버튼 표시 → 클릭 시 실행**(수동 공격 버튼과 동일 UX).
- **B. 프리뷰 경로 = 실행 경로 완전 일치**: COA 버튼 hover 시 노란 프리뷰 선과, 실제 실행 시 파란 점선(은밀기동 확장 경로)이 **완전히 동일**하도록.

## 2. B. 경로 완전 일치 (핵심)
### 원인
`engine.apply_mission_plan(plan)`은 BLUFOR waypoint를 `_stealth_expand_waypoints`로 **은밀기동 우회 경로**로 확장해 적용한다(engine.py:424-425). 반면 COA 프리뷰(`build_coa_preview`)는 계획 원본 waypoint만 그린다 → 두 경로가 다르다.
### 해법
`_stealth_expand_waypoints`는 랜덤 없이 결정적이고, COA 선택 중 시뮬은 정지(상태 고정)다. 따라서 **생성 시점에 미리 확장한 경로를 저장**하고, 실행 시 **재확장 없이 그대로 적용**하면 프리뷰=실행이 정확히 일치한다.
1. **`apply_mission_plan(self, plan, stealth_expand: bool = True)`**: 파라미터 추가. `stealth_expand=False`이면 line 424-425의 확장을 건너뛰고 주어진 waypoint를 그대로 적용. `_objective`(마지막 원본 WP) 캡처는 유지(확장 경로의 마지막 WP == 목표이므로 정상). 기본값 True → 기존 모든 호출 동작 불변(골든 영향 없음).
2. **`expand_plan_waypoints(self, plan: dict) -> dict`**(신규 공개 메서드): plan을 deepcopy 후 각 mission_plan의 BLUFOR 부대 waypoint를 `_stealth_expand_waypoints(unit, wps)`로 확장한 새 plan 반환. 엔진 상태 불변(읽기 전용). `self._lock` 하에서 수행.
3. **`generate_attack_coas`**: 각 COA plan을 `eng.expand_plan_waypoints(coa["plan"])`로 확장해 저장 → `build_coa_preview`가 확장 경로를 그림.
4. **`execute_coa`**: `eng.apply_mission_plan(plan, stealth_expand=False)`로 적용 → 재확장 없음 → 엔진 `u.waypoints` == COA 저장 경로 == 프리뷰. 실시간 지도 파란 점선 = 프리뷰 노란선 완전 동일.
- 인지된 적이 없으면 `_stealth_expand_waypoints`가 원본을 그대로 반환하므로 확장 없이도 일치.

## 3. A. 이벤트 자동 재계획 COA 버튼화
### 세션 상태
- `auto_plan_status`에 `coas: list`(프리뷰 포함)·`coa_gen_id: int`(생성마다 증가) 필드 추가. `__init__`/`reset`에서 초기화(`coas=[]`, `coa_gen_id=0`).
### `generate_attack_coas(session, context_hint: str = "")`
- 선택 파라미터 `context_hint`(이벤트 트리거 설명) 추가 → LLM 쿼리에 덧붙여 이벤트 인지 COA 생성(규칙기반은 상태가 이미 이벤트 반영하므로 무관).
### `execute_auto_attack_plan(session, event_type, *args)` 교체
- 기존 "LLM 1개 생성+즉시적용" 로직을 제거하고:
  1. 트리거 판별·시뮬 정지(공중지원 완료 대기 포함)는 유지.
  2. `res = generate_attack_coas(session, context_hint=trigger_desc)` 호출(엔진 미적용).
  3. `auto_plan_status["coas"] = res["coas"]`, `auto_plan_status["coa_gen_id"] += 1`, `message = log_tag + " — COA 선택 대기"`, `active = False`(계획 완료).
  4. **시뮬 재개 안 함**(정지 유지) — 사용자가 COA 클릭 시 `execute_coa`가 재개.
- 4개 이벤트 타입(detection/cp_threshold/air_hit/target_moved) 모두 동일 처리.
### `execute_coa` 정리
- 성공 시 `session.auto_plan_status["coas"] = []`로 비움(버튼 재출현 방지).
### API
- `/api/auto_plan_status` 응답에 `coas`·`coa_gen_id` 포함(기존 필드 유지).

## 4. 프론트엔드 (`ui/dashboard/index.html`)
- `pollAutoPlan`(1.5s 폴링): `d.coas`가 있고 `d.coa_gen_id`가 마지막 렌더한 값과 다르면 → `renderCoaButtons(d.coas)` **1회 호출** + 채팅 메시지("⚠️ [이벤트 재계획] {message} — COA를 선택하세요"). `let _lastCoaGenId = 0`로 중복 렌더 방지.
- hover 프리뷰·클릭 실행·채팅 수정은 기존 COA 인프라 그대로.

## 5. 테스트 계획
- B: `apply_mission_plan(plan, stealth_expand=False)`가 확장 안 함(주어진 waypoint 그대로 적용) / 기본 True는 기존대로 확장. `expand_plan_waypoints`가 상태 불변·BLUFOR만 확장. generate→execute 후 엔진 `u.waypoints` == COA preview 좌표(위경도 변환 일치).
- A: `execute_auto_attack_plan` 후 `pending_coas`/`auto_plan_status["coas"]` 3개·미적용(BLUFOR waypoints 불변)·`coa_gen_id` 증가·시뮬 정지 유지. `execute_coa` 후 auto_plan_status coas 비움.
- API: `/api/auto_plan_status`에 coas/coa_gen_id 노출.
- 프론트: pollAutoPlan COA 렌더·_lastCoaGenId 문자열 스모크.
- 회귀: 전체 스위트 + import-linter 3 kept + 결정성(apply 기본값 True라 골든 불변).

## 6. 알려진 한계
- 이벤트 COA의 LLM 대체·품질은 라이브 에이전트(vLLM/Gemini) 필요. 규칙기반 백본 항상 동작.
- 경로 완전일치는 "COA 선택 중 시뮬 정지" 전제(현 설계가 정지 유지하므로 성립). 만약 정지 중 외부로 상태가 바뀌면(현재 없음) 재확장과 어긋날 수 있으나 execute가 stealth_expand=False라 저장 경로를 그대로 쓰므로 프리뷰와는 항상 일치.

## 7. 미포함(향후)
- 이벤트별 COA 교리 맞춤화(현재는 상태+트리거 힌트 기반 3 doctrine), COA 성공확률 스코어링.
