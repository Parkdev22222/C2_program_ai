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
  {"call_sign":"<CALLSIGN_1>","support_type":"cas","target":[TX1,TY1],"radius":1500,"delay":6},
  {"call_sign":"<CALLSIGN_2>","support_type":"strike","target":[TX2,TY2],"radius":400,"delay":12}
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

def _blufor_roster_block(state: dict) -> str:
    """계획 대상 BLUFOR 부대를 위경도와 함께 명시적으로 나열 (함수호출 모델용)."""
    try:
        from tools.coord_utils import xy_to_latlon
    except Exception:
        xy_to_latlon = lambda x, y: (x, y)  # noqa: E731
    lines = []
    for u in state.get("units", []):
        if u.get("side") != "BLUFOR" or u.get("status") == "destroyed":
            continue
        lat, lon = xy_to_latlon(u.get("x", 0), u.get("y", 0))
        _cp = u.get("combat_power")
        _cp_s = f"{_cp:.0f}%" if isinstance(_cp, (int, float)) else "미상"
        lines.append(
            f"  - {u['id']} (종류:{u.get('unit_type','?')}, 전투력:{_cp_s}, "
            f"상태:{u.get('status','?')}, 현위치:[{lat:.6f},{lon:.6f}])"
        )
    if not lines:
        return "  (계획 대상 BLUFOR 부대 없음)"
    return "\n".join(lines)


def _opfor_targets_block(state: dict) -> str:
    """탐지된 OPFOR(표적)를 위경도와 함께 명시적으로 나열 (함수호출 모델용)."""
    try:
        from tools.coord_utils import xy_to_latlon
    except Exception:
        xy_to_latlon = lambda x, y: (x, y)  # noqa: E731
    intel = state.get("intelligence", {}).get("BLUFOR", [])
    lines = []
    for e in intel:
        if e.get("status") not in ("detected", "approximate"):
            continue
        lat, lon = xy_to_latlon(e.get("known_x", 0), e.get("known_y", 0))
        _cp = e.get("combat_power")
        _cp_s = f"{_cp:.0f}%" if isinstance(_cp, (int, float)) else "미상"
        lines.append(
            f"  - {e['unit_id']} (종류:{e.get('unit_type','?')}, 전투력:{_cp_s}, "
            f"탐지:{e.get('status')}, 위치:[{lat:.6f},{lon:.6f}])"
        )
    if not lines:
        return "  (탐지된 OPFOR 없음 — air_support_plans 는 빈 배열)"
    return "\n".join(lines)


def _build_mission_query_funccall(state: dict, recon_block: str, attack_pos_block: str,
                                  elev_section: str, air_limit_line: str) -> str:
    """LangGraph(함수호출) 백엔드용 임무계획 쿼리 — 코드 실행 없이 데이터→JSON 직접 구성."""
    roster = _blufor_roster_block(state)
    targets = _opfor_targets_block(state)
    return f"""대대급 C2 AI: BLUFOR 임무계획을 수립하고 워게임에 적용하라.

당신은 코드를 실행하지 않는다. 아래 [제공 데이터]를 **직접 읽고 판단**해 최종 임무계획
JSON을 구성한 뒤, apply_wargame_mission_plan 도구를 function call 로 호출해 적용하라.

⚠️ 가장 중요: [계획 대상 BLUFOR 부대]에 나열된 **모든 부대**에 대해 각각 1개의 임무를
   mission_plans 에 반드시 포함하라. mission_plans 를 절대 빈 배열로 제출하지 말 것.

[계획 대상 BLUFOR 부대] — 이 부대들 각각에 임무를 배정하라
{roster}

[탐지된 OPFOR — 표적 후보]
{targets}

[제공 데이터 — 참고용 계산 결과]
{recon_block}
{attack_pos_block}

[부대별 임무 결정 규칙]
- 정찰: recon_result.status 가 "no_recon_units" 가 아니고 recon 경로가 있으면 해당 정찰부대만
  mission_type="recon" 으로 배정하고 그 waypoints 를 사용한다. 정찰부대가 없으면(현 시나리오는
  UAV 정찰로 적 위치 파악) 모든 부대를 전투/방어 임무로 배정한다.
- 자주포(포병) 부대: 전방 이동을 지양하고 현 후방 위치를 유지(mission_type="hold" 또는 "defend").
  포병은 자동으로 사거리 내 표적에 화력지원하므로 전진 공격 임무를 주지 말 것.
- 그 외 각 BLUFOR 전투부대(기계화보병/전차/대전차 등):
  · 전투력 CP ≥ 30% → mission_type = "attack" 또는 "flank" (측방 기동 우선)
  · 전투력 CP < 30% → mission_type = "defend" 또는 "withdraw"
  · attack/flank 부대는 담당할 표적을 "target_unit_id" 에 [탐지된 OPFOR] 중 하나의 ID로 반드시 명시.
  · waypoints 는 attack_positions_result.unit_key_highground 중 해당 unit_id 의 position([lat,lon])을
    우선 사용(있으면). 없으면 표적으로 향하는 은밀·유리한 접근 경로 3~5개를 직접 산정.
  · 아군이 적과 근접 교전 중인 표적에는 공중지원 method 를 strike(정밀타격)로.

[공중지원·포병 규칙]
{air_limit_line}
- air CAS(cas/strike/helicopter)는 위 잔여 횟수 내에서만. 포병(artillery)은 횟수 제한 없이 동시 투사 가능.
- ★ 위협 우선순위 화력 집중: attack_positions_result.air_support_schedule(공중)과
  artillery_support_schedule(포병)을 모두 air_support_plans 로 생성하라.
  위협도 상위 표적은 **같은 target 좌표**에 공중지원(cas/strike/helicopter)과 포병(artillery)을
  **동시에** 배정할 수 있다(별개 항목 2개, 같은 [lat,lon]). 화력을 집중해 고위협 표적을 조기 제압하라.
- air_support_plans[].target 좌표는 반드시 [탐지된 OPFOR] 의 위치([lat,lon])를 그대로 사용(임의 좌표 금지).
- 탐지된 OPFOR 가 없으면 air_support_plans = [].

[좌표 규칙]
- 모든 waypoints·target 은 위경도(WGS84) 소수점 6자리. 예:[38.081081,127.101248].
- 미터 정수([9000,8000])나 단순 정수([9,8]) 사용 금지.
[지형고도] 작전지역=철원(DMZ인근), lat 38.0~38.27 / lon 127.0~127.34.
{elev_section}

[실행 절차 — 반드시 준수]
1) 위 규칙으로 final_plan 을 구성한다. mission_plans 에는 위 [계획 대상 BLUFOR 부대]가 모두 포함돼야 한다.
2) ⚠️ apply_wargame_mission_plan 등 어떤 적용/조회 도구도 호출하지 마라. 워게임 적용은 시스템이 수행한다.
3) 당신의 최종 응답은 오직 아래 형식의 JSON 블록 하나여야 한다. 다른 설명 텍스트·도구 호출 금지.
   mission_plans 가 비어 있으면 안 된다 (모든 대상 부대 포함).

[출력 JSON 형식]
```json
{{"reasoning":"한국어 판단근거",
"mission_plans":[
  {{"company_id":"Delta","mission_type":"recon","waypoints":[[위도,경도]],"objective":"정찰 목표"}},
  {{"company_id":"실제부대ID","mission_type":"attack","target_unit_id":"담당 OPFOR ID","waypoints":[[위도,경도]],"objective":"목표"}}
],
"air_support_plans":[
  {{"call_sign":"호출부호1","support_type":"cas","target":[위도,경도],"radius":1500,"delay":6}},
  {{"call_sign":"호출부호2","support_type":"artillery","target":[위도,경도],"radius":2500,"delay":30}}
]}}
```
(위 예시처럼 위협도 상위 표적에는 공중지원(cas/strike/helicopter)과 포병(artillery)을 **같은 target 좌표**로 동시 배정 가능)
[형식 참고 예시] (좌표·ID는 placeholder — 실제 데이터로 대체)
{_FEW_SHOT_EXAMPLES}"""


def build_mission_query(state: dict) -> str:
    """
    BattlefieldAgent.run()에 전달할 공격 임무계획 쿼리 문자열 생성.

    에이전트 처리 순서:
      (recommend_recon_routes / get_optimal_attack_positions 는 이 함수가 미리 실행해
       결과를 [제공 데이터]로 프롬프트에 주입 → 에이전트는 툴로 호출하지 않는다.)
      1. 최종 임무계획 JSON 생성   → recon_result·attack_positions_result·situation_result 기반 직접 결정
      2. apply_wargame_mission_plan(plan_json=..., dry_run=False)  → 즉시 적용
      3. 응답에 JSON 블록 출력

    ※ situation_result(전장상황)는 에이전트 실행 시 온톨로지 블록으로 자동 주입된다.
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

    # recommend_recon_routes / get_optimal_attack_positions 를 미리 실행해 결과를 프롬프트에 주입.
    # (에이전트가 직접 툴을 호출하지 않고, 아래 [제공 데이터]의 JSON을 변수로 그대로 사용)
    import json as _json
    try:
        from tools.wargame_recon_tool import recommend_recon_routes as _reco_fn
        _recon_result = _reco_fn()
    except Exception as _re:
        _recon_result = {"status": "no_recon_units", "error": str(_re)}
    try:
        from tools.wargame_attack_advisor_tool import get_optimal_attack_positions as _gap_fn
        _attack_positions_result = _gap_fn()
    except Exception as _ae:
        _attack_positions_result = {"status": "error", "error": str(_ae)}
    recon_block = (
        "[recommend_recon_routes() 결과 — recon_result]\n"
        f"```json\n{_json.dumps(_recon_result, ensure_ascii=False)}\n```"
    )
    attack_pos_block = (
        "[get_optimal_attack_positions() 결과 — attack_positions_result]\n"
        f"```json\n{_json.dumps(_attack_positions_result, ensure_ascii=False)}\n```"
    )

    # 백엔드에 맞춘 프롬프트 선택:
    #   langgraph(기본, 함수호출 모델) → 코드 실행 없이 데이터→JSON 직접 구성
    #   smolagents(CodeAgent)          → 아래 Python 코드 지향 프롬프트
    import os as _os
    _backend = _os.environ.get("C2_AGENT_BACKEND", "langgraph").strip().lower()
    if _backend != "smolagents":
        return _build_mission_query_funccall(
            state, recon_block, attack_pos_block, elev_section, air_limit_line
        )

    query = f"""대대급 C2 AI: BLUFOR 임무계획을 수립하라.

⚠️ 필수: 아래 [제공 데이터]를 근거로 최종 임무계획 JSON을 직접 구성하라.
예시의 좌표·부대명·호출부호를 절대 그대로 사용 금지. 모든 값은 제공 데이터에서 가져와야 한다.
⚠️ 코드 첫 줄에 반드시 `import json` 을 실행하라. json 없이 json.dumps() 호출 시 NameError 발생.

※ situation_result / recon_result / attack_positions_result 는 모두 아래 제공된 JSON을 변수로 그대로 사용한다 (별도 툴 호출 없음).
  - situation_result: 자동 주입된 [현재 전장 상황] 블록. 키 situation_result["units"] = 부대 리스트("unit_id"/"side"/"combat_power"/"status"/"lat"/"lon").
  - recon_result / attack_positions_result: 아래 [제공 데이터]의 JSON.

[제공 데이터]
{recon_block}
{attack_pos_block}

[필수 툴 호출 순서]
1. [EXAONE4 직접 판단] 아래 Python 코드 구조로 최종 임무계획 JSON을 직접 구성하라:

   import json

   mission_plans = []

   # ① Delta 정찰부대 임무 — recon_result 기반
   if recon_result.get("status") != "no_recon_units" and recon_result.get("mission_plans"):
       delta_mp = recon_result["mission_plans"][0]
       mission_plans.append({{
           "company_id": delta_mp["company_id"],
           "mission_type": "recon",
           "waypoints": delta_mp["waypoints"],
           "objective": delta_mp.get("objective", "정찰")
       }})

   # ② BLUFOR 공격부대 임무 — attack_positions_result["unit_key_highground"] 근거로 직접 결정
   #    각 원소: {{"unit_id","target_unit_id","position":[lat,lon],"elevation_m","elevation_advantage"}}
   #    → 부대별 주요 고지(position)를 waypoint로, 담당 타겟(target_unit_id)을 그대로 사용
   #    situation_result["units"] 중 side=="BLUFOR" 에서 각 부대 ID·전투력 참조
   #    • CP >= 30% → attack 또는 flank   • CP < 30% → defend 또는 withdraw
   highground = {{h["unit_id"]: h for h in attack_positions_result.get("unit_key_highground", [])}}
   for unit in situation_result["units"]:
       if unit.get("side") != "BLUFOR":
           continue
       if unit["unit_id"] == "Delta" or unit["status"] == "destroyed":
           continue
       cp = unit["combat_power"]
       if cp <= 5:
           continue
       # ★ attack/flank 임무는 담당할 적 부대를 target_unit_id로 반드시 명시 ★
       #   (highground[unit_id]["target_unit_id"]). 부대는 경유지 도달 후 이 표적을 지속 추격한다.
       hg = highground.get(unit["unit_id"])
       mission_plans.append({{
           "company_id": unit["unit_id"],
           "mission_type": "<CP·전황 기반 직접 결정: attack|flank|defend|withdraw|hold>",
           "target_unit_id": "<hg['target_unit_id'] — attack/flank일 때 필수, 그 외 생략>",
           "waypoints": [hg["position"]] if hg else [],  # 위경도 소수점6자리 (주요 고지)
           "objective": "<담당 OPFOR 공략 또는 방어 목표>"
       }})

   # ③ 항공 자산 CAS 계획 — attack_positions_result["air_support_schedule"] 사용 (아군 전용)
   air_support_plans = []
   # 각 원소: {{"priority","target_unit_id","target_type","target":[lat,lon],"method"}}
   # → 잔여 공중지원 횟수(air CAS는 5회 제한) 내에서 우선순위대로 target·method 그대로 사용
   # ★ 아군 부대가 적과 인접(근접 교전)한 표적은 반드시 정밀타격(strike, 좁은 반경) 사용 ★
   #   (스케줄의 method가 이미 strike로 설정됨 — 광역 cas 사용 금지: 근접 상황 부적합)
   # 참고: 포병(자주포 부대) 화력지원은 별도 상시 지원(횟수 제한 없음)이며 항공 CAS와 동시 투사됨.

   final_plan = {{
       "reasoning": "<EXAONE4 전략 판단 근거 — 한국어>",
       "mission_plans": mission_plans,
       "air_support_plans": air_support_plans
   }}

2. apply_wargame_mission_plan(plan_json=json.dumps(final_plan, ensure_ascii=False), dry_run=False)
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
[공중지원유형] cas(근접항공,반경1500m,6s지연) strike(정밀타격,400m,12s) artillery(포병,2500m,30s) helicopter(헬기,1000m,60s)
⚠️ [공중지원·포격 목표 좌표 강제 규칙]
   air_support_plans 의 target 좌표는 반드시 [현재 전장 상황] 또는 assess_recon_need() 에서
   조회한 탐지된(detected) OPFOR 부대의 known_lat, known_lon 값을 그대로 사용해야 합니다.
   임의 추정 좌표·waypoint 중간점·아군 위치 등을 target으로 사용 절대 금지.
   예: Red1 위치가 known_lat=37.074775, known_lon=127.141013 이면 → "target": [37.074775, 127.141013]
⚠️ [담당 표적 지정 규칙]
   • mission_type이 attack 또는 flank인 부대는 "target_unit_id"에 담당할 detected OPFOR unit_id를 반드시 명시
   • 부대는 waypoints(경유지)를 통과한 뒤 target_unit_id 부대의 현재 위치를 지속 추격·공격한다
   • waypoints는 표적으로의 은밀·유리한 접근 경로(경유지)이고, 최종 교전 위치는 표적 실시간 위치로 자동 갱신됨
   • defend/withdraw/hold/recon 부대는 target_unit_id 생략
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
  {{"company_id":"실제부대ID","mission_type":"attack|flank","target_unit_id":"담당 detected OPFOR ID","waypoints":[[위도소수,경도소수]],"objective":"목표"}},
  {{"company_id":"실제부대ID","mission_type":"defend|withdraw|hold","waypoints":[[위도소수,경도소수]],"objective":"목표"}}
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

        # ── 규칙 기반 공중지원 계획 ──────────────────────────────────────
        # 공중지원 잔여 횟수 확인
        air_use   = state.get("air_use_count", {})
        air_limit = state.get("air_use_limit", 5)
        remaining = max(0, air_limit - air_use.get("BLUFOR", 0))

        air_support_plans = []
        if remaining > 0:
            # detected 적 인텔만 추출, 전투력 높은 순(위협 우선) 정렬
            detected = [
                e for e in state.get("intelligence", {}).get("BLUFOR", [])
                if e.get("status") == "detected"
            ]
            detected.sort(key=lambda e: e.get("combat_power") or 0, reverse=True)

            call_signs = ["EAGLE-1", "EAGLE-2", "EAGLE-3", "VIPER-1", "VIPER-2"]
            for idx, enemy in enumerate(detected[:remaining]):
                cs = call_signs[idx] if idx < len(call_signs) else f"STRIKE-{idx + 1}"
                unit_type = enemy.get("unit_type", "")
                # 기갑(전차·장갑차) → helicopter, 그 외 → cas
                if any(kw in unit_type for kw in ("전차", "tank", "장갑", "armor")):
                    s_type, radius, delay = "helicopter", 1000, 60
                else:
                    s_type, radius, delay = "cas", 1500, 6
                air_support_plans.append({
                    "call_sign":    cs,
                    "support_type": s_type,
                    "target":       [int(enemy["known_x"]), int(enemy["known_y"])],
                    "radius":       radius,
                    "delay":        delay,
                })

        reasoning = f"[규칙 기반] OPFOR 집결점 ({int(op_cx)},{int(op_cy)}) 공격."
        if air_support_plans:
            reasoning += f" 탐지 OPFOR {len(air_support_plans)}개에 공중지원 할당."

        return {
            "reasoning":        reasoning,
            "mission_plans":    plans,
            "air_support_plans": air_support_plans,
        }
