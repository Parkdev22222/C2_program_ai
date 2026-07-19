# 기동 중 교전 시 정지·교전 + BLUFOR 고지 기동 설계

- 날짜: 2026-07-19
- 대상: `c2.application.simulation.engine`(`_move_units` 중심), 특성화 골든
- import-linter 계약 영향 없음

## 1. 목표
1. 아군·적군 부대가 기동(waypoint 이동) 중 **직사 교전이 발생하면 그 자리에 정지**해 교전한다(양측).
2. BLUFOR는 정지에 그치지 않고 **교전을 지속하며 주변 고지(고도+엄폐 유리 지점)로 룰 기반 소폭 기동**한다(LLM 명령 아님).
3. 교전 종료(적 격멸·사거리 밖 이탈) 시 **원래 임무(waypoint/추격)를 자동 재개**한다.

## 2. 교전 판정 — `_in_direct_combat(u)` (신규)
- 반환: 상호 직사 사거리 내 가장 가까운 적 `Unit`, 없으면 `None`.
- "상호 직사 사거리": 적과의 거리 `d`에 대해 `_engagement_factor(u.unit_type, d) > 0`(내가 사격 가능) **또는** `_engagement_factor(e.unit_type, d) > 0`(적이 사격 가능)이면 교전 접촉.
- 자주포(`unit_type=="자주포"`)는 직사 교전 대상이 아니므로 **u가 자주포면 None**, 적 후보에서도 **자주포 제외**(기존 `_resolve_combat`과 동일 규칙).
- 격멸/비활성 적 제외.

## 3. `_move_units` 정지 분기
- 각 부대의 waypoint 전진 로직 **직전**(suppressed 스킵 다음)에 교전 판정 삽입:
  - `enemy = self._in_direct_combat(u)`
  - `enemy is not None`이면:
    - `u.side == "BLUFOR"` → `self._blufor_combat_reposition(u, enemy, dt)` 호출(고지 기동)
    - 그 외(OPFOR) → 이동 없음(그 자리 정지·교전)
    - 두 경우 모두 `continue` — **waypoint 전진 스킵, waypoint 보존**.
- waypoint를 pop/clear하지 않으므로 교전 종료 시 다음 틱부터 기존 waypoint 전진/추격이 자동 재개된다.
- 자주포는 `_in_direct_combat`이 None → 기존 이동/standoff 유지(정지 규칙 미적용).

## 4. BLUFOR 고지 기동 — `_blufor_combat_reposition(u, enemy, dt)` (신규)
- `_blufor_llm_units`/`mission_lock`과 무관하게 적용(룰 기반 전술 계층).
- 지형 점수 헬퍼: `_ground_score(x, y) = terrain.elevation(x, y) + terrain.cover_factor(x, y) * _COMBAT_COVER_WEIGHT`.
- 후보: 현 위치 기준 8방위(45°) × 반경 `_COMBAT_REPOS_RADIUS` 지점. 각 후보 `(cx, cy)`는 맵 범위 클램프(0~29999).
- 채택 조건(둘 다 만족):
  1. `_ground_score(cx, cy) > _ground_score(u.x, u.y)` — 현 위치보다 유리.
  2. `_engagement_factor(u.unit_type, dist_to_enemy(cx,cy)) > 0` — 이동 후에도 적을 내 사거리에 유지(교전 이탈 방지).
- 최고 점수 후보로 **교전 기동 속도**로 소폭 이동:
  `step = min(u.max_speed * _COMBAT_MOVE_MULT * terrain.movement_speed_factor(u.x,u.y) * dt, dist)`.
- 채택 후보가 없으면 이동 없음(현 위치 고수, 계속 교전).
- 매 틱 재평가 → 점진적으로 국지 고지 선점.

## 5. 상수
- `_COMBAT_REPOS_RADIUS = 300.0` (후보 평가 반경 m)
- `_COMBAT_MOVE_MULT = 0.4` (교전 기동 속도 배율)
- `_COMBAT_COVER_WEIGHT = 150.0` (엄폐 점수 가중치, `_find_opfor_defensive_positions`와 동일 기준)

## 6. 골든 재생성 (필수)
정지·고지 기동으로 900틱 궤적이 바뀐다 → `engine_900tick_seed42.json` 재생성. 결정성(a==b) 유지, 교전 발생 확인.

## 7. 테스트 계획
- `_in_direct_combat`: 사거리 내 적 → 반환, 사거리 밖 → None, 자주포 → None.
- 정지(OPFOR): 이동 중 OPFOR가 사거리 내 적과 만나면 위치가 거의 불변(waypoint 전진 안 함) + waypoint 보존.
- 정지(BLUFOR): 교전 중 waypoint 방향 전진이 아니라 고지 기동(점수 비감소, 적 사거리 유지).
- 고지 기동: 반복 틱 후 `_ground_score`가 시작보다 ≥(향상 또는 동일), 적이 여전히 사거리 내.
- 재개: 적 제거 후 다음 틱부터 waypoint 방향으로 전진 재개.
- 골든: 재생성 후 determinism green + 교전 발생.

## 8. 미포함(향후)
- OPFOR 고지 기동(요청은 BLUFOR 한정), 후퇴/철수 판단, 다중 적 우선순위 정교화.
