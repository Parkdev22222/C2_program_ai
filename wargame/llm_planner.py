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

# ── Few-Shot 예시 (출력 형식 전용 — 좌표·부대명은 placeholder) ────────
# ⚠️ 아래 예시의 좌표·부대명·호출부호는 형식 설명용 placeholder입니다.
# 절대로 예시 값을 그대로 사용하지 말고, 반드시 툴 호출 결과를 사용하십시오.

_FEW_SHOT_EXAMPLES = """\
[형식예시1] 전력우세·공중지원 병행 (※ 좌표·ID는 placeholder — 실제 툴 결과로 대체)
{"reasoning":"<툴 조회 결과 기반 한국어 판단근거>",
 "mission_plans":[
  {"company_id":"Delta","mission_type":"recon","waypoints":[[RX1,RY1],[RX2,RY2],[RX3,RY3]],"objective":"측방 관측·추적"},
  {"company_id":"<BLUFOR_ID_A>","mission_type":"flank","waypoints":[[X1,Y1],[X2,Y2],[X3,Y3]],"objective":"<OPFOR_ID_1> 측방 공격"},
  {"company_id":"<BLUFOR_ID_B>","mission_type":"attack","waypoints":[[X4,Y4],[X5,Y5]],"objective":"<OPFOR_ID_2> 정면 압박"}
 ],
 "air_support_plans":[
  {"call_sign":"<CALLSIGN_1>","support_type":"cas","target":[TX1,TY1],"radius":1500,"delay":60},
  {"call_sign":"<CALLSIGN_2>","support_type":"strike","target":[TX2,TY2],"radius":400,"delay":120}
 ]}

[형식예시2] 전력열세·방어+포병지원 (※ 좌표·ID는 placeholder — 실제 툴 결과로 대체)
{"reasoning":"<툴 조회 결과 기반 한국어 판단근거>",
 "mission_plans":[
  {"company_id":"Delta","mission_type":"recon","waypoints":[[RX1,RY1],[RX2,RY2]],"objective":"측방 경계"},
  {"company_id":"<BLUFOR_ID_A>","mission_type":"defend","waypoints":[[X1,Y1]],"objective":"고지 방어"},
  {"company_id":"<BLUFOR_ID_B>","mission_type":"withdraw","waypoints":[[X2,Y2],[X1,Y1]],"objective":"<BLUFOR_ID_A> 합류"}
 ],
 "air_support_plans":[
  {"call_sign":"<CALLSIGN_1>","support_type":"artillery","target":[TX1,TY1],"radius":2500,"delay":30}
 ]}

[형식예시3] 공중지원 없음·포위 섬멸 (※ 좌표·ID는 placeholder — 실제 툴 결과로 대체)
{"reasoning":"<툴 조회 결과 기반 한국어 판단근거>",
 "mission_plans":[
  {"company_id":"Delta","mission_type":"recon","waypoints":[[RX1,RY1],[RX2,RY2],[RX3,RY3]],"objective":"미탐지 적 부대 추적"},
  {"company_id":"<BLUFOR_ID_C>","mission_type":"attack","waypoints":[[X1,Y1],[X2,Y2]],"objective":"정면 압박"},
  {"company_id":"<BLUFOR_ID_D>","mission_type":"flank","waypoints":[[X3,Y3],[X4,Y4]],"objective":"후방 차단"}
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

    try:
        from tools.coord_utils import xy_to_latlon as _xy_to_latlon
    except Exception:
        _xy_to_latlon = None

    # 부대 위치 고도 (한 줄씩)
    for u in state.get("units", []):
        if u["status"] == "destroyed":
            continue
        elev  = _terrain.elevation(u["x"], u["y"])
        cover = _terrain.cover_factor(u["x"], u["y"])
        if _xy_to_latlon:
            lat, lon = _xy_to_latlon(u["x"], u["y"])
            lines.append(
                f"{u['id']}(lat={lat},lon={lon}) "
                f"→{elev:.0f}m 엄폐{cover:.2f}"
            )
        else:
            lines.append(
                f"{u['id']}({int(u['x'])},{int(u['y'])}) "
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
                if _xy_to_latlon:
                    g_lat, g_lon = _xy_to_latlon(xs, ys)
                    grid.append(f"(lat={g_lat},lon={g_lon})={_terrain.elevation(xs,ys):.0f}m")
                else:
                    grid.append(f"({int(xs)},{int(ys)})={_terrain.elevation(xs,ys):.0f}m")
        lines.append("접촉구역:" + " ".join(grid))

        # TOP2 고지
        high = []
        for r in range(8):
            for c in range(8):
                xs = max(0, min(29999, cx - span*1.5 + c * span*3/7))
                ys = max(0, min(29999, cy - span*1.5 + r * span*3/7))
                high.append((_terrain.elevation(xs, ys), xs, ys))
        high.sort(reverse=True)
        if _xy_to_latlon:
            lines.append("고지TOP2:" + " ".join(
                f"(lat={_xy_to_latlon(xs,ys)[0]},lon={_xy_to_latlon(xs,ys)[1]})={e:.0f}m"
                for e, xs, ys in high[:2]
            ))
        else:
            lines.append("고지TOP2:" + " ".join(
                f"({int(xs)},{int(ys)})={e:.0f}m" for e, xs, ys in high[:2]
            ))

    return "\n".join(lines)


# ── 프롬프트 빌더 ─────────────────────────────────────────────────

def build_mission_query(state: dict) -> str:
    """
    BattlefieldAgent.run()에 전달할 공격 임무계획 쿼리 문자열 생성.

    에이전트 툴 활용 순서:
      1. get_wargame_situation()         → 부대 위치·전투력·행동 조회
      2. assess_recon_need()             → OPFOR 탐지 현황 (detected 목표만 공격)
      3. predict_opfor_routes()          → 탐지된 OPFOR 예상 기동 경로 분석
      4. get_optimal_attack_positions(opfor_routes_json=<3번 predicted_routes JSON>)
                                         → 경로 차단 보너스 반영 최적 공격 위치 추천
      5. strategy_advisor_tool(query=..., additional_context=<4번 결과>)
                                         → EXAONE Deep이 공격 위치 결과 검토·조언
      6. 최종 임무계획 JSON 생성         → 4번+5번 종합, detected OPFOR만 목표
      7. apply_wargame_mission_plan(plan_json=..., dry_run=False)  → 즉시 적용
      8. 응답에 JSON 블록 출력

    ※ 현재 부대 위치·전투력 등 전장상황은 에이전트가 tool 호출로 직접 조회한다.
    """
    elev_section = _sample_elevation_map(state)

    air_use = state.get("air_use_count", {})
    air_limit = state.get("air_use_limit", 5)
    blu_used = air_use.get("BLUFOR", 0)
    blu_remaining = max(0, air_limit - blu_used)
    air_reset_at = state.get("air_reset_at", 0)
    cur_tick = state.get("tick", 0)
    ticks_to_reset = max(0, air_reset_at - cur_tick)
    air_limit_line = (
        f"[공중지원 잔여 횟수] BLUFOR 현재 {blu_remaining}/{air_limit}회 사용 가능"
        f" (잔여 {ticks_to_reset}틱 후 리셋). 잔여 횟수가 0이면 air_support_plans는 빈 배열로."
    )

    query = f"""대대급 C2 AI: BLUFOR 임무계획을 수립하라.

⚠️ 필수: 아래 툴을 반드시 순서대로 호출하여 실제 전장 데이터를 수집한 후 임무계획을 수립하라.
예시의 좌표·부대명·호출부호를 절대 그대로 사용 금지. 모든 값은 툴 호출 결과에서 가져와야 한다.

[필수 툴 호출 순서]
1. (자동 재계획 시 생략 가능) get_wargame_situation()
   → 이미 [현재 전장 상황]으로 제공된 경우 호출 불필요. situation 변수로 그대로 사용.
2. assess_recon_need()
   → OPFOR 탐지 현황 확인. detected 부대만 공격 목표로 사용
3. recommend_recon_routes()
   → Delta 정찰부대의 경로 생성 → recon_result에 저장
   → recon_result["mission_plans"]의 첫 번째 항목을 Delta 임무로 사용
   → status가 "no_recon_units"이면 Delta 임무 제외, 나머지는 항상 포함
4. predict_opfor_routes()
   → 탐지된 OPFOR 부대의 예상 기동 경로(정면/우측우회/좌측우회) 분석
   → 결과를 opfor_routes_result에 저장
   → import json; opfor_routes_json = json.dumps(opfor_routes_result["predicted_routes"])
5. get_optimal_attack_positions(opfor_routes_json=opfor_routes_json)
   → 적 예상 경로 차단 보너스가 반영된 최적 공격 위치 추천 (결과를 attack_positions_result에 저장)
6. strategy_advisor_tool(
     query="탐지된 OPFOR에 대한 공격 임무계획 전술 검토를 요청합니다. 적 예상 기동 경로와 공격 위치 추천 결과를 바탕으로 최적 기동 방향, 경로 차단 위치, 공중지원 배치, 우선순위를 조언해주세요. 또한 Delta 정찰부대의 정찰 경로(recon_result의 waypoints)가 공격 임무를 효과적으로 지원하는지, 경로 개선이 필요한지도 검토해주세요.",
     additional_context=str(attack_positions_result) + "\n\n[Delta 정찰 경로]\n" + str(recon_result)
   )
   → EXAONE Deep 전술 조언 수집 (결과를 deep_advice에 저장)
7. 위 1~6 결과를 종합하여 최종 임무계획 JSON 생성
   → 실제 부대 ID, 실제 좌표만 사용 / detected OPFOR만 목표
   → Delta(정찰부대): recon_result["mission_plans"][0] 그대로 mission_plans에 추가
   → 나머지 BLUFOR 부대: attack/flank/defend/hold 임무 부여
8. apply_wargame_mission_plan(plan_json=<JSON문자열>, dry_run=False)
   → 워게임 즉시 적용

[지형고도] 작전지역=철원(DMZ인근), 좌표=위경도(WGS84), lat=북위(38.0~38.27), lon=동경(127.0~127.34)
실제 SRTM 30m DEM 기반 — 고도 8m~925m, 광주산맥·철원평야·한탄강 계곡 반영
⚠️ waypoints·target 좌표는 반드시 위경도(WGS84) 소수점 6자리로 표기
   예: [38.081081, 127.101248] → 이는 내부 미터 기준 (9000,9000)에 해당
   절대 미터 정수([9000,8000]) 또는 단순 정수([9,8]) 사용 금지
{elev_section}

[출력 형식 예시] ← 형식만 참고. 좌표·ID는 placeholder이므로 절대 그대로 사용 금지
{_FEW_SHOT_EXAMPLES}

{air_limit_line}
[공중지원유형] cas(근접항공,반경1500m,60s지연) strike(정밀타격,400m,120s) artillery(포병,2500m,30s) helicopter(헬기,1000m,60s)
⚠️ [공중지원·포격 목표 좌표 강제 규칙]
   air_support_plans 의 target 좌표는 반드시 get_wargame_situation() 또는 assess_recon_need() 에서
   조회한 탐지된(detected) OPFOR 부대의 known_lat, known_lon 값을 그대로 사용해야 합니다.
   임의 추정 좌표·waypoint 중간점·아군 위치 등을 target으로 사용 절대 금지.
   예: Red1 위치가 known_lat=37.074775, known_lon=127.141013 이면 → "target": [37.074775, 127.141013]
[규칙] 좌표는 반드시 위경도(WGS84) 소수점 6자리, WP 3~5개, CP<30%→defend/withdraw, 고지선점·측방기동 고려
⚠️ [공중지원 적극 활용 규칙] 교전 초반 위치 확인(detected) 적에게 공중지원 적극 투사
   - 공중지원 잔여 횟수>0이면 detected OPFOR 1개 이상에 반드시 air_support_plans 할당
   - cas/strike로 선제 타격 → 적 전투력 조기 약화. artillery는 클러스터 적에, helicopter는 기갑 목표에 우선
⚠️ [Delta 정찰부대 규칙]
   • Delta는 반드시 mission_type="recon"으로 mission_plans에 포함 (공격/돌격 임무 절대 금지)
   • Delta의 waypoints는 반드시 recommend_recon_routes() 결과의 mission_plans[0]["waypoints"]를 그대로 사용 (이미 위경도 형식)
   • recon_result["status"]가 "no_recon_units"인 경우에만 Delta를 mission_plans에서 제외
최종 JSON 출력(설명금지):
```json
{{"reasoning":"툴 조회 결과 기반 한국어 판단근거",
"mission_plans":[
  {{"company_id":"Delta","mission_type":"recon","waypoints":[[위도소수,경도소수]],"objective":"측방 관측·추적 또는 미탐지 부대 확인"}},
  {{"company_id":"실제부대ID","mission_type":"attack|defend|flank|withdraw|hold","waypoints":[[위도소수,경도소수]],"objective":"목표"}}
],
"air_support_plans":[{{"call_sign":"호출부호","support_type":"cas|strike|artillery|helicopter","target":[위도소수,경도소수],"radius":반경m정수,"delay":지연초정수}}]}}
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
                "objective": f"OPFOR 격멸 ({int(op_cx)},{int(op_cy)})"
            })

        return {
            "reasoning": f"[규칙 기반] OPFOR 집결점 ({int(op_cx)},{int(op_cy)}) 공격.",
            "mission_plans": plans,
        }
