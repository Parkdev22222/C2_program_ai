# 포병 과보상 완화 — 4개 브레이크 설계 문서

- 날짜: 2026-07-18
- 대상: `c2.domain.wargame`(ControlPoint), `c2.application.simulation`(engine/scenario), `c2.presentation.web`(state), `ui/dashboard`, 특성화 골든
- import-linter 계약 영향 없음

## 1. 배경 / 목표

현재 워게임은 순수 전멸 승리조건이라, 무제한·전지적·정적 포병 스팸으로 적 CP를 0까지 갈아
이기는 것이 최적해다. 현실의 네 가지 브레이크(탄약·대포병·격멸한계·목표점령)를 도입해
"포격만 퍼부어 이기는" 지배전략을 제거한다.

## 2. ① 탄약 — 지속사격 예산 + 재보급 쿨다운

`_resolve_indirect_fire`(engine.py) 대상. 자주포(SPG)에 사격 예산을 부여.

- 상수: `_SPG_FIRE_BUDGET = 300.0`(연속사격 가능 게임초), `_SPG_RESUPPLY_COOLDOWN = 240.0`(재보급 대기 게임초).
- 엔진 상태: `_spg_ammo: Dict[str, float]`(남은 사격 게임초), `_spg_resupply_until: Dict[str, float]`(재보급 완료 game_time). `reset()`에서 초기화(빈 dict).
- 동작(각 SPG 매 틱):
  - `game_time < _spg_resupply_until[id]` 이면 재보급 중 → 사격 스킵(continue).
  - 사격하면 `_spg_ammo[id] -= dt`(초기 미존재 시 `_SPG_FIRE_BUDGET`에서 시작).
  - `_spg_ammo[id] <= 0` 이면 `_spg_resupply_until[id] = game_time + _SPG_RESUPPLY_COOLDOWN`, `_spg_ammo[id] = _SPG_FIRE_BUDGET`(재보급 완료 시점에 만충), 이번 틱 사격 스킵.
  - 로그: 재보급 진입 시 `AMMO_RESUPPLY` 이벤트 1회.

## 3. ② 대포병 — shoot-and-scoot 압박

정적 포격을 자살행위로 만든다. `_resolve_indirect_fire`에서 사격하는 SPG에 적용.

- 상수: `_CB_EXPOSURE_DELAY = 120.0`(정적 사격 후 대포병 개시 게임초), `_CB_DAMAGE_RATE = 80.0`(%/h, 최대 램프), `_CB_RAMP = 180.0`(램프 게임초), `_CB_MOVE_RESET = 300.0`(이 거리 이상 이동 시 타이머 리셋, m).
- 엔진 상태: `_spg_static_fire: Dict[str, float]`(같은 자리서 사격한 누적 게임초). `reset()`에서 초기화.
- 이동 감지: `_prev_positions[id]` 대비 현재 위치 이동거리 > `_CB_MOVE_RESET` → `_spg_static_fire[id] = 0.0`.
- 사격 중(이번 틱 표적 있음)일 때만 `_spg_static_fire[id] += dt`.
- 대포병 피해: `over = _spg_static_fire[id] - _CB_EXPOSURE_DELAY`; `over > 0`이면
  `ramp = min(1.0, over / _CB_RAMP)`, `cb_dmg = _CB_DAMAGE_RATE * ramp * (dt/3600)`.
  `spg.combat_power = max(0.0, spg.combat_power - cb_dmg)` + `_check_blufor_cp_threshold`.
  로그: 유의피해(누적 임계) 시 `COUNTER_BATTERY` 이벤트.
- 효과: ~2게임분 이상 같은 자리서 쏘면 급격히 피해 → 사격 후 진지변환 유도. 이동하면 안전.

## 4. ③ 격멸 상한 — 간접포만 CP 15% 바닥

간접포는 제압까지만, 최종 격멸은 직사(기동부대) 몫.

- 상수: `_INDIRECT_CP_FLOOR = 15.0`.
- `_resolve_indirect_fire`의 **적 피해** 및 **아군 오사(fratricide) 피해** 양쪽에 적용:
  - `if victim.combat_power > _INDIRECT_CP_FLOOR: victim.combat_power = max(_INDIRECT_CP_FLOOR, victim.combat_power - damage)`
  - 이미 `<= _INDIRECT_CP_FLOOR`(직사로 이미 저하됨)면 간접포는 **무피해**(no-op) — 간접포 단독 격멸 불가.
- 공중지원(`_resolve_air_support`)은 미적용(5회 제한이라 스팸 아님) — 기존대로 격멸 가능.
- 참고: CP 15%는 `SUPPRESSED_THRESHOLD`(30) 이하라 간접포로 제압 상태까지는 몰아넣을 수 있음.

## 5. ④ 승리조건 — 통제구역 3곳 다수유지

### 5-1. 도메인
- `c2.domain.wargame`에 값 객체 `ControlPoint`(id: str, x: float, y: float) 추가(순수, frozen dataclass).
- 3개 통제구역(경합지대, BLUFOR 5~10k와 OPFOR 18~23k 사이):
  - 통제-알파 (12_000, 14_000)
  - 통제-브라보 (15_000, 15_000)
  - 통제-찰리 (14_000, 12_000)

### 5-2. 엔진
- 상수: `_CP_CAPTURE_RADIUS = 2_000.0`(점령 판정 반경 m), `_CP_HOLD_TO_WIN = 300.0`(다수 유지 승리 게임초).
- 엔진 상태(reset에서 초기화): `_control_points: List[ControlPoint]`(고정 3개), `_cp_owner: Dict[str, Optional[str]]`(cp_id→side/None), `_cp_majority_since: Dict[str, Optional[float]]`(side→다수 달성 시작 game_time), `_cp_winner: Optional[str]`.
- `_update_control_points(dt)` (매 틱 `_tick`에서 호출):
  - 각 CP: 반경 내 활성 BLUFOR/OPFOR 수 비교 → 다수 측이 소유. 동수/0이면 이전 소유 유지.
  - 각 side가 ≥2개 CP 소유 시 `_cp_majority_since[side]` 설정(미설정 시 now), 아니면 None으로.
  - 어느 side가 `game_time - _cp_majority_since[side] >= _CP_HOLD_TO_WIN` 이면 `_cp_winner = side`.
- `_check_winner()` = 전멸 판정 OR `_cp_winner`(전멸 우선순위는 기존대로, CP승리 병행). 부수효과 없음(추적은 `_update_control_points`에서만).
- 로그: CP 소유 변경 시 `CP_CAPTURE`, CP승리 시 기존 `ENDEX` 경로.

### 5-3. state / UI
- `get_state()`에 `control_points` 키 추가:
  `[{"id","x","y","owner","blufor_near","opfor_near"}]`.
- `ui/dashboard/index.html`: 3개 통제구역 마커 렌더(소유 측 색: BLUFOR 파랑/OPFOR 빨강/중립 회색) + 반경 원. 웹 API `/api/state`는 이미 `get_state()`를 반환하므로 데이터는 자동 전달.

## 6. ⑤ 골든 재생성 (필수)

4개 변경으로 900틱 궤적·결과가 바뀐다. `tests/characterization/test_engine_determinism.py`의
`engine_900tick_seed42.json`을 재생성·재커밋. 결정성(a==b) 유지 확인. 교전 발생(일부 CP<100) 확인.

## 7. 테스트 계획
- 탄약: SPG가 예산 소진 후 재보급 동안 사격 안 함(피해 정지) → 쿨다운 후 재개.
- 대포병: 정적 사격 지속 시 SPG CP 감소 + COUNTER_BATTERY 로그; 이동 시 타이머 리셋으로 피해 미발생.
- 격멸상한: 간접포로 CP 15% 바닥 확인(그 이하로 안 내려감), 공중지원은 격멸 가능.
- 통제구역: 반경 내 BLUFOR 다수 → owner=BLUFOR; 2곳 300초 유지 → winner=BLUFOR; get_state에 control_points 포함.
- UI: 대시보드가 control_points 마커를 렌더(수동/스모크).
- 골든: 재생성 후 determinism green + 교전 발생.

## 8. 미포함(향후)
- 공중지원 탄약/재사용 정교화, 통제구역 점령 진행바(부분점령), 비대칭 전투서열.
