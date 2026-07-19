# 사거리 밖 일방 피격 시 돌입/이탈 설계

- 날짜: 2026-07-19
- 대상: `c2.application.simulation.engine`(`_move_units` 교전 분기), 특성화 골든
- import-linter 계약 영향 없음

## 1. 배경 / 목표
직전 기능(기동 중 교전 정지 + BLUFOR 고지 기동)에서, 부대가 **자기 직사 사거리 밖의 적에게 일방적으로 피격**당하면 반격도 고지 기동도 못 하고 그 자리에서 죽는 갭이 있었다. 이를 보완해:
- **CP ≥ 임계** → 적 사거리 안으로 **돌입해 반격**
- **CP < 임계** → **엄폐·후방으로 이탈**
- **양측(BLUFOR·OPFOR) 모두** 적용.

## 2. 트리거 (`_move_units` 교전 분기)
- 현재 분기: `_contact = self._in_direct_combat(u)`; 접촉 시 BLUFOR는 `_blufor_combat_reposition`, OPFOR는 정지.
- 신규: 접촉한 최근접 적이 **내 직사 사거리 밖**이면(`_engagement_factor(u.unit_type, u.distance_to(_contact)) <= 0`) = 일방 피격 → `_combat_out_of_range_response(u, _contact, dt)`(양측).
- 내 사거리 안이면 기존 유지(BLUFOR 고지 기동 / OPFOR 정지).
- 분기 구조:
  ```
  _contact = self._in_direct_combat(u)
  if _contact is not None:
      if _engagement_factor(u.unit_type, u.distance_to(_contact)) <= 0:
          self._combat_out_of_range_response(u, _contact, dt)   # 양측 돌입/이탈
      elif u.side == "BLUFOR":
          self._blufor_combat_reposition(u, _contact, dt)
      # else OPFOR 사거리 내 → 정지
      continue
  ```
  (최근접 적이 내 사거리 밖이면 모든 접촉 적이 내 사거리 밖 = 일방 피격. 내 사거리는 unit_type 고정 disc이므로 최근접만 확인하면 충분.)

## 3. `_combat_out_of_range_response(u, enemy, dt)` (신규, 양측)
- 결정: `u.combat_power >= _CHARGE_CP_THRESHOLD` → **돌입**, 아니면 **이탈**.
- 후보 평가: 현 위치 8방위(45°) × 반경 `_COMBAT_REPOS_RADIUS`(기존 300m 재사용), 맵 클램프(0~29999).
  - 돌입: 적과의 거리가 **감소**하는 후보만.
  - 이탈: 적과의 거리가 **증가**하는 후보만.
  - 후보 중 `terrain.cover_factor`(엄폐)가 가장 큰 방향 선택.
- 후보 없으면 폴백: 돌입=적 좌표 방향, 이탈=적 반대 방향(`2*u - enemy`) 직진(클램프).
- 이동: `step = min(u.max_speed * _ESCAPE_MOVE_MULT * terrain.movement_speed_factor(u.x,u.y) * dt, dist)`; 방향 단위벡터로 전진. `dist<=0`이면 이동 없음.
- waypoint는 건드리지 않음(돌입→사거리 진입 시 정상 교전 전환, 이탈→접촉 해소 시 waypoint 재개).

## 4. 상수
- `_CHARGE_CP_THRESHOLD = 50.0` (이 이상 돌입, 미만 이탈)
- `_ESCAPE_MOVE_MULT = 0.8` (돌입/이탈 기동 속도 배율 — 긴급 기동)

## 5. 골든 재생성 (필수)
돌입/이탈로 궤적 변화 → `engine_900tick_seed42.json` 재생성. 결정성(a==b) 유지, 교전 발생 확인.

## 6. 테스트 계획
- 돌입(양측): CP 100 부대가 사거리 밖 적에게 피격 → 1틱 후 적과의 거리 감소.
- 이탈: CP 40 부대가 사거리 밖 적에게 피격 → 1틱 후 적과의 거리 증가.
- 양측: OPFOR도 동일하게 CP 기준 돌입/이탈.
- 회귀: 사거리 내 교전은 기존대로(BLUFOR 고지 기동 / OPFOR 정지) — 사거리 내 적이면 out-of-range 응답 미발동.
- 골든: 재생성 후 determinism green + 교전 발생.

## 7. 미포함(향후)
- 다중 위협 우선순위, 엄폐물 정밀 경로탐색, 돌입 중 재-표적 지정.
