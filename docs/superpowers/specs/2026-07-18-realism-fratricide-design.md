# 워게임 현실성 튜닝 + 아군 오사(fratricide) + 공격위치 툴 반영 — 설계 문서

- 날짜: 2026-07-18
- 대상: `c2.application.simulation`(engine/scenario), `c2.presentation.tools`(attack advisor), 특성화 골든
- import-linter 계약 영향 없음

## 1. 배경 / 목표

현재 시나리오는 구조·상성은 현실적이나 물리 충실도가 낮다. 세 가지 현실성 갭을 보정하고,
포격·공중지원에 **아군 오사**를 도입하며, 공격위치 추천 툴이 **아군이 폭발 반경에 드는지**를
표시하도록 한다.

## 2. A. 현실성 튜닝

### A-1. 기동 속도 (약 2.5배 상향)
`c2.application.simulation.scenario`의 `setup_cheorwon_bn()` 하드코딩 `max_speed`와
`UNIT_TYPE_SPECS`(커스텀 시나리오용) **양쪽**을 동일하게 갱신한다.

| 병종 | 기존 max_speed | 신규 max_speed |
|------|----------------|----------------|
| 전차 | 2.0 | **6.0** |
| 기계화보병 | 2.5 | **5.0** |
| 대전차 | 2.2 | **5.5** |
| 자주포 | 1.8 | **4.0** |
| 정찰 | 4.5 | **7.0** |

### A-2. 배치 밀도 (대대 정면 ~5km)
`_BLUFOR_ZONE`/`_OPFOR_ZONE`와 `setup_cheorwon_bn()`의 6개 부대 시작좌표를 축소·재배치한다.

- `_BLUFOR_ZONE` = x 5_000~10_000, y 5_000~10_000
- `_OPFOR_ZONE`  = x 18_000~23_000, y 18_000~23_000

신규 BLUFOR 좌표(자주포 후방):
| 부대 | x | y |
|------|---|---|
| 보병1중대 | 7_000 | 6_000 |
| 보병2중대 | 8_000 | 7_500 |
| 보병3중대 | 9_500 | 9_000 |
| 전차중대 | 6_000 | 7_000 |
| 대전차중대 | 9_000 | 6_000 |
| 자주포중대 | 5_500 | 5_500 |

신규 OPFOR 좌표(자주포 후방):
| 부대 | x | y |
|------|---|---|
| 적보병1중대 | 20_000 | 19_000 |
| 적보병2중대 | 19_000 | 20_500 |
| 적보병3중대 | 18_500 | 18_500 |
| 적전차중대 | 21_000 | 20_000 |
| 적대전차중대 | 21_500 | 21_500 |
| 적자주포중대 | 22_500 | 22_500 |

최근접(보병3중대↔적보병3중대) ≈ 13.0km → 5 m/s로 접적.

### A-3. 포병 사거리 지도 스케일화 + 대포병 강화
- BLUFOR 자주포중대 `indirect_range`: 40_000 → **15_000**
- OPFOR 적자주포중대 `indirect_range`: 60_000 → **18_000**
- `_COUNTER_BATTERY_DETECT_PROB`: 0.35 → **0.55**
- 실제 제원(K9 40km / 곡산 60km)은 주석에 유지하되, 게임 유효사거리는 지도 스케일에 맞춤.
- 효과: 후방 자주포는 초기 사거리 밖 → 전진 진지 선정이 실제 결정거리가 됨. 사격 시 노출↑.

## 3. B. 아군 오사 (fratricide) — 양측 대칭, 적과 동일 피해

### B-1. 공중지원 (`_resolve_air_support`, engine.py:1268~)
현재 `side_targets[air.side]`(반대편)만 피해. **같은 편(`air.side`) 활성 부대**도 반경 내면
동일 공식으로 피해를 적용한다.

- 피해 공식은 적 피해와 동일(proximity/cover/eff_dt_h/랜덤 0.7~1.3, min_damage 포함).
- 아군 victim에도 `_check_blufor_cp_threshold(u, before)` 호출(BLUFOR면 임계값 트리거).
- 아군 victim에는 `on_blufor_air_hit` 재계획 콜백/적 인텔 노출 **미적용**(오사는 적 화력 아님).
- damage ≥ 3.0 시 로그 이벤트 타입 `FRATRICIDE_AIR`:
  `[{air.side}] {call_sign}⚠아군오사→{u.id}: -{d:.1f}% CP (거리{dist/1000:.1f}km)`

### B-2. 간접사격 (`_resolve_indirect_fire`, engine.py:1092~)
현재 `enemies`(반대편)만 피해. **같은 편(`spg.side`) 활성 부대**도 AoE(`aoe_radius`) 내면
동일 공식으로 피해를 적용한다.

- 피해 공식 동일: `_matchup_factor(spg.unit_type, friendly.unit_type)` 등 그대로.
- 아군 victim에 `_check_blufor_cp_threshold` 호출. 적 인텔 노출 블록/`_indirect_accum` 적 로깅은 미적용.
- 로그 이벤트 타입 `FRATRICIDE_INDIRECT`(누적 임계 `_INDIRECT_LOG_THRESHOLD` 동일 적용):
  `{spg.id}(자주포)⚠아군오사→{friendly.id}({type}): -{acc:.1f}% CP 누적 (AoE반경{r:.0f}m)`
- 자기 자신(spg==friendly)은 제외.

## 4. C. 공격위치 툴 — 오사 위험 플래그

`c2.presentation.tools.wargame_attack_advisor_tool.get_optimal_attack_positions()`의
`air_support_schedule`·`artillery_support_schedule` 각 항목에 필드 추가.

- 방법별 폭발 반경 = `AIR_SUPPORT_PRESETS`(c2.domain.wargame.unit)의 radius:
  cas 1_500 / strike 400 / artillery 2_500 / helicopter 1_000.
- 표적 좌표(known_x/known_y) 기준 반경 내 **활성 BLUFOR** 부대를 위험 부대로 산출.

```json
"friendly_fire_risk": {
  "blast_radius_m": 1500,
  "in_blast": true,
  "endangered_units": [{"unit_id": "보병1중대", "dist_m": 900}]
}
```
- `in_blast=true`면 `reason` 앞에 `⚠️ 아군 N개 오사 위험 — ` 경고 부가.
- **자동 제거/전환 없음**(LLM 판단 존중). 플래그·경고만.

## 5. D. 골든 재생성 (필수)

`tests/characterization/test_engine_determinism.py`가 `engine_900tick_seed42.json`을 비교한다.
속도·사거리·오사 변경으로 궤적이 바뀌어 골든이 깨지므로:
1. 변경 완료 후 동일 절차(seed 42, 900틱)로 골든을 재생성·재커밋.
2. 결정성 자체(동일 시드 2회 실행 a==b)는 유지되어야 함 — 재생성 후에도 통과 확인.

## 6. 테스트 계획
- scenario: 새 속도/좌표/indirect_range 단위 검증(속도값, 구역 범위, 좌표 in-zone).
- engine 오사: 아군을 표적 반경에 놓고 공중/간접사격 → 아군 CP 감소 + FRATRICIDE 로그 확인.
- engine 오사 미적용 경계: 반경 밖 아군은 무피해.
- tool: 아군을 표적 반경에 놓고 friendly_fire_risk.in_blast=true + endangered_units 확인; 반경 밖이면 false.
- 골든: 재생성 후 determinism 테스트 green.

## 7. 미포함(향후)
- shoot-and-scoot(사격 후 자주포 자동 진지변환) — 별도 스펙.
- 비대칭 전투서열(북한 포병·전차 밀도) — 별도 스펙.
