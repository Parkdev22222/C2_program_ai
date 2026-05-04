"""
LLM 기반 임무계획 생성기.

BattlefieldAgent.run()을 통해 현재 구축된 LLM 에이전트 시스템을 사용합니다.
프롬프트에 아군/적군 위치·전력, 고도맵 샘플, few-shot 예시를 포함합니다.

우선순위:
  1. BattlefieldAgent (EXAONE4 / smolagents) — gradio_app의 _agent 사용
  2. 규칙 기반 폴백 (에이전트 없거나 실패 시)
"""

import json
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

# ── Few-Shot 예시 (모델에게 출력 형식을 학습시킴) ────────────────────

_FEW_SHOT_EXAMPLES = """
=== 임무계획 JSON 출력 예시 ===

[예시 1] 아군 전력 우세, 적 거리 8km
상황: Alpha(100%, 7.5km,5.0km,고도45m), Bravo(100%, 7.5km,6.5km,고도52m)
      vs Red1(85%, 15km,13km,고도180m), Red2(90%, 16km,13.5km,고도165m)
      중앙 능선(12km,12km 부근) 고도 280m — 고지 선점 필요

출력:
```json
{
  "reasoning": "적이 고지(180m)를 선점하고 있어 정면 돌격 시 불리하다. Alpha는 서측 우회로로 접근해 적 측방을 위협하고, Bravo는 중앙 능선 고지를 선점하여 화력 우위를 확보한 후 협공한다.",
  "mission_plans": [
    {
      "company_id": "Alpha",
      "mission_type": "flank",
      "waypoints": [[9000, 8000], [11000, 10000], [13000, 12000], [15000, 13000]],
      "objective": "서측 우회 기동으로 Red1 측방 공격"
    },
    {
      "company_id": "Bravo",
      "mission_type": "attack",
      "waypoints": [[9500, 9000], [12000, 11500], [14000, 13000], [16000, 13500]],
      "objective": "중앙 능선 고지 선점 후 Red2 정면 공격"
    }
  ]
}
```

[예시 2] 아군 열세(전투력 저하), 방어 전환
상황: Alpha(35%, 13km,12km,고도120m), Bravo(28%, 12km,13km,고도95m)
      vs Red1(70%, 17km,14km,고도200m), Red2(65%, 18km,13km,고도185m)
      아군 고지(14km,12km) 고도 240m — 방어 진지 적합

출력:
```json
{
  "reasoning": "아군 전투력이 30% 내외로 저하되었고 적이 고지에서 화력 우위를 점하고 있다. Alpha와 Bravo 모두 인근 고지로 철수하여 방어 진지를 구축하고 증원을 기다린다.",
  "mission_plans": [
    {
      "company_id": "Alpha",
      "mission_type": "defend",
      "waypoints": [[14000, 12000], [14000, 12000]],
      "objective": "고지(14km,12km) 방어 진지 구축 및 현위치 고수"
    },
    {
      "company_id": "Bravo",
      "mission_type": "withdraw",
      "waypoints": [[13000, 12500], [14000, 12000]],
      "objective": "Alpha 진지로 철수 후 통합 방어"
    }
  ]
}
```

[예시 3] 교전 중, 적 1개 중대 전투불능
상황: Alpha(80%, 16km,14km,고도210m), Bravo(72%, 15km,15km,고도195m)
      vs Red1(destroyed), Red2(45%, 20km,19km,고도130m)
      적 Red2 후방 보급로 차단 가능

출력:
```json
{
  "reasoning": "Red1이 전투불능 상태이며 Red2는 전투력이 45%로 저하되었다. Alpha가 고지 우위를 유지하며 정면 압박하고, Bravo는 우회하여 Red2 후방 보급로를 차단해 포위 섬멸한다.",
  "mission_plans": [
    {
      "company_id": "Alpha",
      "mission_type": "attack",
      "waypoints": [[17000, 15000], [18000, 17000], [20000, 19000]],
      "objective": "Red2 정면 압박 및 고지 우위 유지"
    },
    {
      "company_id": "Bravo",
      "mission_type": "flank",
      "waypoints": [[17000, 17000], [20000, 20000], [22000, 21000]],
      "objective": "Red2 후방 우회 차단으로 포위 섬멸"
    }
  ]
}
```
"""

# ── 고도맵 샘플링 ─────────────────────────────────────────────────

def _sample_elevation_map(state: dict) -> str:
    """
    작전 지역 핵심 지점의 고도를 샘플링하여 텍스트로 반환.
    - 각 부대 위치
    - 아군-적군 중간 접촉 예상 구역 (5x5 격자)
    """
    try:
        from wargame.terrain import terrain as _terrain
    except Exception:
        return "(고도 정보 없음)"

    lines = []

    # 1. 각 부대 위치 고도
    lines.append("[ 부대 위치 고도 ]")
    for u in state.get("units", []):
        if u["status"] == "destroyed":
            continue
        elev = _terrain.elevation(u["x"], u["y"])
        cover = _terrain.cover_factor(u["x"], u["y"])
        lines.append(
            f"  {u['id']:6s} ({u['x']/1000:.1f}km, {u['y']/1000:.1f}km) "
            f"→ 고도 {elev:.0f}m  엄폐 {cover:.2f}"
        )

    # 2. 접촉 예상 구역 격자 샘플 (아-적 중간 영역)
    blufor = [u for u in state["units"] if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
    opfor  = [u for u in state["units"] if u["side"] == "OPFOR"  and u["status"] != "destroyed"]

    if blufor and opfor:
        bl_cx = sum(u["x"] for u in blufor) / len(blufor)
        bl_cy = sum(u["y"] for u in blufor) / len(blufor)
        op_cx = sum(u["x"] for u in opfor)  / len(opfor)
        op_cy = sum(u["y"] for u in opfor)  / len(opfor)

        # 중간 지점 중심 ±4km 격자 (4x4 = 16 샘플)
        cx = (bl_cx + op_cx) / 2
        cy = (bl_cy + op_cy) / 2
        span = 4000
        steps = 4

        lines.append(f"\n[ 접촉 예상 구역 고도 샘플 (중심 {cx/1000:.1f}km,{cy/1000:.1f}km ±4km) ]")
        row_lines = []
        for row in range(steps):
            ys = cy - span + row * (2 * span / (steps - 1))
            cols = []
            for col in range(steps):
                xs = cx - span + col * (2 * span / (steps - 1))
                elev = _terrain.elevation(max(0, xs), max(0, ys))
                cols.append(f"({xs/1000:.1f}k,{ys/1000:.1f}k)={elev:.0f}m")
            row_lines.append("  " + "  ".join(cols))
        lines.extend(row_lines)

        # 최고 고지 힌트
        high_pts = []
        for row in range(10):
            for col in range(10):
                xs = cx - span * 1.5 + col * (span * 3 / 9)
                ys = cy - span * 1.5 + row * (span * 3 / 9)
                xs = max(0, min(29999, xs))
                ys = max(0, min(29999, ys))
                elev = _terrain.elevation(xs, ys)
                high_pts.append((elev, xs, ys))
        high_pts.sort(reverse=True)
        top3 = high_pts[:3]
        lines.append("\n[ 작전 지역 최고 고지 TOP3 ]")
        for elev, xs, ys in top3:
            lines.append(f"  ({xs/1000:.1f}km, {ys/1000:.1f}km) → {elev:.0f}m ← 고지 선점 고려")

    return "\n".join(lines)


# ── 프롬프트 빌더 ─────────────────────────────────────────────────

def build_mission_query(state: dict) -> str:
    """
    BattlefieldAgent.run()에 전달할 전체 쿼리 문자열 생성.
    구성: 시스템 지시 + 전장 현황 + 고도맵 + few-shot + 출력 요청
    """
    # ── 부대 현황 ──
    unit_lines = []
    for u in state.get("units", []):
        if u["status"] == "destroyed":
            status_str = "전투불능(×)"
        elif u["status"] == "suppressed":
            status_str = f"제압({u['combat_power']:.0f}%)"
        else:
            status_str = f"전투가능({u['combat_power']:.0f}%)"
        unit_lines.append(
            f"  [{u['side']}] {u['id']:6s}: 위치=({u['x']/1000:.1f}km, {u['y']/1000:.1f}km)  "
            f"고도={u.get('elevation', 0):.0f}m  {status_str}"
        )

    # ── 위협 분석 ──
    blufor = [u for u in state["units"] if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
    opfor  = [u for u in state["units"] if u["side"] == "OPFOR"  and u["status"] != "destroyed"]
    if blufor and opfor:
        bl_cx = sum(u["x"] for u in blufor) / len(blufor)
        bl_cy = sum(u["y"] for u in blufor) / len(blufor)
        op_cx = sum(u["x"] for u in opfor)  / len(opfor)
        op_cy = sum(u["y"] for u in opfor)  / len(opfor)
        dist  = ((bl_cx - op_cx)**2 + (bl_cy - op_cy)**2)**0.5
        threat_line = (
            f"BLUFOR 중심: ({bl_cx/1000:.1f}km, {bl_cy/1000:.1f}km)  "
            f"OPFOR 중심: ({op_cx/1000:.1f}km, {op_cy/1000:.1f}km)  "
            f"거리: {dist/1000:.1f}km"
        )
    else:
        threat_line = "전투 종료 또는 전멸"

    elev_section = _sample_elevation_map(state)
    winner = state.get("winner")

    query = f"""[군사 작전 임무계획 생성 요청]

당신은 대대급 전투 C2 AI입니다.
아래 전장 상황, 고도 정보, 출력 예시를 참고하여
BLUFOR 각 중대의 최적 임무계획을 JSON으로 출력하세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ 현재 전장 상황 (게임 시간: {state.get('game_time_str','00:00:00')})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{chr(10).join(unit_lines)}

위협 분석: {threat_line}
{"※ 전투 종료: " + winner + " 승리" if winner else ""}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ 작전 지역 지형 고도 정보
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{elev_section}

좌표계: x=동쪽(m), y=북쪽(m), 지도 범위 0~30,000m
고도 우위: 공격자가 80m 이상 높으면 화력 +30%, 80m 이상 낮으면 -20%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ 임무계획 JSON 출력 예시 (Few-Shot)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_FEW_SHOT_EXAMPLES}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ 출력 규칙
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 좌표는 m 단위 정수 (0~30000 범위)
2. 각 중대에 3~5개 웨이포인트
3. 전투력 30% 이하 부대 → defend 또는 withdraw 우선
4. 고지 선점, 상호지원, 측방 기동을 전술적으로 고려
5. 반드시 아래 JSON 형식만 출력 (설명·마크다운 없음)

최종 출력 (```json 블록 사용):
```json
{{
  "reasoning": "한국어로 전술 판단 근거 2~3문장",
  "mission_plans": [
    {{
      "company_id": "Alpha",
      "mission_type": "attack",
      "waypoints": [[x1,y1],[x2,y2],[x3,y3]],
      "objective": "임무 목표"
    }}
  ]
}}
```"""
    return query


# ── 플래너 클래스 ─────────────────────────────────────────────────

class MissionPlanner:
    """BattlefieldAgent 연동 임무계획 생성기."""

    def plan(self, state: dict, agent=None) -> dict:
        """
        전장 상태를 받아 임무계획 dict 반환.

        Args:
            state: WargameEngine.get_state() 반환값
            agent: BattlefieldAgent 인스턴스 (없으면 규칙 기반 폴백)
        """
        if agent is None:
            log.info("에이전트 없음 → 규칙 기반 임무계획 생성")
            return self._rule_based(state)

        query = build_mission_query(state)
        log.info("BattlefieldAgent에 임무계획 쿼리 전송...")

        try:
            raw = agent.run(query, reset=False)
            result = self._parse_json(str(raw))
            if result and "mission_plans" in result:
                log.info(f"임무계획 수신 완료: {len(result['mission_plans'])}개 중대")
                return result
            log.warning(f"JSON 파싱 실패, 원문: {str(raw)[:200]}")
        except Exception as e:
            log.warning(f"에이전트 호출 실패: {e}")

        log.info("규칙 기반 폴백으로 전환")
        return self._rule_based(state)

    # ── JSON 파싱 ─────────────────────────────────────────────────

    def _parse_json(self, text: str) -> Optional[dict]:
        text = text.strip()
        # ```json ... ``` 블록 우선 추출
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
        else:
            # 코드블록 없으면 첫 { ... } 추출
            m2 = re.search(r"\{[\s\S]*\}", text)
            if m2:
                text = m2.group()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # ── 규칙 기반 폴백 ────────────────────────────────────────────

    def _rule_based(self, state: dict) -> dict:
        """에이전트 없거나 실패 시 규칙으로 임무계획 생성."""
        opfor_alive = [u for u in state["units"]
                       if u["side"] == "OPFOR" and u["status"] != "destroyed"]
        if not opfor_alive:
            return {"reasoning": "모든 적 전멸.", "mission_plans": []}

        op_cx = sum(u["x"] for u in opfor_alive) / len(opfor_alive)
        op_cy = sum(u["y"] for u in opfor_alive) / len(opfor_alive)

        blufor = [u for u in state["units"]
                  if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
        plans = []

        for i, u in enumerate(blufor):
            cp = u["combat_power"]
            if cp <= 5:
                continue
            if cp < 30:
                plans.append({
                    "company_id": u["id"],
                    "mission_type": "defend",
                    "waypoints": [[u["x"], u["y"]]],
                    "objective": "현위치 방어"
                })
                continue
            offset = 500 if i % 2 == 0 else -500
            plans.append({
                "company_id": u["id"],
                "mission_type": "attack",
                "waypoints": [
                    [round(u["x"] + (op_cx - u["x"]) * 0.35 + offset),
                     round(u["y"] + (op_cy - u["y"]) * 0.35)],
                    [round(u["x"] + (op_cx - u["x"]) * 0.70),
                     round(u["y"] + (op_cy - u["y"]) * 0.70 + offset * 0.6)],
                    [round(op_cx + offset * 0.5), round(op_cy)],
                ],
                "objective": f"OPFOR 격멸 ({op_cx/1000:.1f}km,{op_cy/1000:.1f}km)"
            })

        return {
            "reasoning": f"[규칙 기반] OPFOR 집결점 ({op_cx/1000:.1f}km, {op_cy/1000:.1f}km) 공격.",
            "mission_plans": plans,
        }
