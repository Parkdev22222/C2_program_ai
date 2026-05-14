"""
LLM 기반 임무계획 생성기.

BattlefieldAgent.run()을 통해 현재 구축된 LLM 에이전트 시스템을 사용합니다.
프롬프트에 아군/적군 위치·전력, 고도맵 샘플, few-shot 예시를 포함합니다.

우선순위:
  1. BattlefieldAgent (EXAONE4 / smolagents) — gradio_app의 _agent 사용
  2. 규칙 기반 폴백 (에이전트 없거나 실패 시)
"""

import ast
import json
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

# ── Few-Shot 예시 (간결 버전) ─────────────────────────────────────

_FEW_SHOT_EXAMPLES = """\
[예시1] 전력우세·공중지원 병행
{"reasoning":"CAS로 Red3 전차 집결지 제압 후 Alpha 측방 기동.",
 "mission_plans":[
  {"company_id":"Alpha","mission_type":"flank","waypoints":[[9000,8000],[13000,11000],[15000,13000]],"objective":"Red1 측방 공격"},
  {"company_id":"Bravo","mission_type":"attack","waypoints":[[10000,9000],[14000,13000]],"objective":"Red2 정면 압박"}
 ],
 "air_support_plans":[
  {"call_sign":"VIPER-1","support_type":"cas","target":[21000,21000],"radius":1500,"delay":60},
  {"call_sign":"THUNDER-1","support_type":"strike","target":[23000,20000],"radius":400,"delay":120}
 ]}

[예시2] 전력열세·방어+포병지원
{"reasoning":"전투력 30%↓, 고지 철수. 포병으로 적 접근로 차단.",
 "mission_plans":[
  {"company_id":"Alpha","mission_type":"defend","waypoints":[[14000,12000]],"objective":"고지 방어"},
  {"company_id":"Bravo","mission_type":"withdraw","waypoints":[[13000,12500],[14000,12000]],"objective":"Alpha 합류"}
 ],
 "air_support_plans":[
  {"call_sign":"ARTY-1","support_type":"artillery","target":[16000,14000],"radius":2500,"delay":30}
 ]}

[예시3] 공중지원 없음·포위 섬멸
{"reasoning":"Red1 전투불능. Delta 고속 우회, Charlie 정면으로 Red2 포위.",
 "mission_plans":[
  {"company_id":"Charlie","mission_type":"attack","waypoints":[[18000,16000],[21000,19000]],"objective":"정면 압박"},
  {"company_id":"Delta","mission_type":"flank","waypoints":[[20000,21000],[23000,22000]],"objective":"후방 차단"}
 ],
 "air_support_plans":[]}"""

# ── 고도맵 샘플링 ─────────────────────────────────────────────────

def _sample_elevation_map(state: dict) -> str:
    """작전 지역 핵심 지점 고도 요약 (토큰 최소화)."""
    try:
        from wargame.terrain import terrain as _terrain
    except Exception:
        return "(고도 정보 없음)"

    lines = []

    # 부대 위치 고도 (한 줄씩)
    for u in state.get("units", []):
        if u["status"] == "destroyed":
            continue
        elev  = _terrain.elevation(u["x"], u["y"])
        cover = _terrain.cover_factor(u["x"], u["y"])
        lines.append(
            f"{u['id']}({u['x']/1000:.0f}k,{u['y']/1000:.0f}k) "
            f"→{elev:.0f}m 엄폐{cover:.2f}"
        )

    # 접촉 예상 구역 3×3 샘플
    blufor = [u for u in state["units"] if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
    opfor  = [u for u in state["units"] if u["side"] == "OPFOR"  and u["status"] != "destroyed"]
    if blufor and opfor:
        cx = (sum(u["x"] for u in blufor)/len(blufor) + sum(u["x"] for u in opfor)/len(opfor)) / 2
        cy = (sum(u["y"] for u in blufor)/len(blufor) + sum(u["y"] for u in opfor)/len(opfor)) / 2
        span, steps = 3000, 3
        grid = []
        for r in range(steps):
            for c in range(steps):
                xs = cx - span + c * span
                ys = cy - span + r * span
                xs = max(0, min(29999, xs))
                ys = max(0, min(29999, ys))
                grid.append(f"({xs/1000:.0f}k,{ys/1000:.0f}k)={_terrain.elevation(xs,ys):.0f}m")
        lines.append("접촉구역:" + " ".join(grid))

        # TOP2 고지
        high = []
        for r in range(8):
            for c in range(8):
                xs = max(0, min(29999, cx - span*1.5 + c * span*3/7))
                ys = max(0, min(29999, cy - span*1.5 + r * span*3/7))
                high.append((_terrain.elevation(xs, ys), xs, ys))
        high.sort(reverse=True)
        lines.append("고지TOP2:" + " ".join(
            f"({xs/1000:.0f}k,{ys/1000:.0f}k)={e:.0f}m" for e, xs, ys in high[:2]
        ))

    return "\n".join(lines)


# ── 프롬프트 빌더 ─────────────────────────────────────────────────

def build_mission_query(state: dict) -> str:
    """
    BattlefieldAgent.run()에 전달할 공격 임무계획 쿼리 문자열 생성.

    에이전트 툴 활용 순서:
      1. get_wargame_situation()         → 부대 위치·전투력·행동 조회
      2. assess_recon_need()             → OPFOR 탐지 현황 (detected 목표만 공격)
      3. get_optimal_attack_positions()  → 최적 공격 위치·기동 방향 추천
      4. strategy_advisor_tool(query=..., additional_context=<3번 결과>)
                                         → EXAONE Deep이 공격 위치 결과 검토·조언
      5. 최종 임무계획 JSON 생성         → 3번+4번 종합, detected OPFOR만 목표
      6. apply_wargame_mission_plan(plan_json=..., dry_run=False)  → 즉시 적용
      7. 응답에 JSON 블록 출력

    ※ 현재 부대 위치·전투력 등 전장상황은 에이전트가 tool 호출로 직접 조회한다.
    """
    elev_section = _sample_elevation_map(state)

    query = f"""대대급 C2 AI: BLUFOR 임무계획을 JSON으로 출력하라.
현재 전장 상황(부대 위치·전투력·인텔 등)은 반드시 도구(tool)를 호출하여 조회하라.

[지형고도] 좌표(m),x=동쪽,y=북쪽,범위0~30000,고도우위±40%
{elev_section}

[출력예시]
{_FEW_SHOT_EXAMPLES}

[공중지원유형] cas(근접항공,반경1500m,60s지연) strike(정밀타격,400m,120s) artillery(포병,2500m,30s) helicopter(헬기,1000m,60s)
[규칙] 좌표m정수,WP 3~5개,CP<30%→defend/withdraw,고지선점·측방기동 고려,공중지원은 필요 시만 사용
아래 JSON만 출력(설명금지):
```json
{{"reasoning":"한국어 판단근거",
"mission_plans":[{{"company_id":"ID","mission_type":"attack|defend|flank|withdraw|hold","waypoints":[[x,y]],"objective":"목표"}}],
"air_support_plans":[{{"call_sign":"호출부호","support_type":"cas|strike|artillery|helicopter","target":[x,y],"radius":반경m,"delay":지연초}}]}}
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
                n_air = len(result.get("air_support_plans", []))
                log.info(f"임무계획 수신 완료: {len(result['mission_plans'])}개 중대, 공중지원 {n_air}건")
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
        # JSON 파싱 시도
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Python dict 리터럴(홑따옴표) 폴백
        try:
            result = ast.literal_eval(text)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
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
