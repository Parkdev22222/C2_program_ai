"""
COA(Course of Action) 분석 도구

현재 워게임 상태를 기반으로 여러 행동 방책(COA)을 평가하고
각 COA의 장단점, 위험도, 권장 순위를 반환합니다.
"""
import logging
from smolagents import tool

logger = logging.getLogger(__name__)

_wargame_engine = None


def register_wargame_engine(engine):
    """UI에서 WargameEngine 인스턴스를 등록."""
    global _wargame_engine
    _wargame_engine = engine


@tool
def analyze_coa_wargame(coa_list: list, objective: str = "") -> dict:
    """
    복수의 행동 방책(COA)을 현재 워게임 상태에 대입하여 비교 평가합니다.

    Args:
        coa_list: 평가할 COA 목록. 각 항목은 dict 형태:
            [
              {
                "coa_id": "COA-1",
                "name": "정면 공격",
                "description": "Alpha/Bravo가 정면 돌파",
                "mission_plans": [
                  {"company_id": "Alpha", "mission_type": "attack", "waypoints": [[x,y]], "objective": "..."},
                  ...
                ]
              },
              ...
            ]
        objective: 전체 작전 목표 (선택).

    Returns:
        {
          "status": "success" | "engine_not_ready" | "error",
          "objective": str,
          "evaluated": [...],
          "recommended_coa": str,
          "summary": str
        }
    """
    if _wargame_engine is None:
        return {"status": "engine_not_ready", "message": "워게임 엔진이 초기화되지 않았습니다."}

    if not coa_list:
        return {"status": "error", "message": "coa_list가 비어 있습니다."}

    try:
        from tools.mission_plan_validator import validate_mission_plan
        state = _wargame_engine.get_state()
    except Exception as e:
        return {"status": "error", "message": f"상태 조회 실패: {e}"}

    blufor_units = {u["id"]: u for u in state.get("units", []) if u["side"] == "BLUFOR"}
    opfor_units = [u for u in state.get("units", []) if u["side"] == "OPFOR"]

    evaluated = []
    for coa in coa_list:
        coa_id = coa.get("coa_id", f"COA-{len(evaluated)+1}")
        name = coa.get("name", coa_id)
        mission_plans = coa.get("mission_plans", [])

        plan_dict = {"mission_plans": mission_plans}
        validation = validate_mission_plan(plan_dict)

        pros: list = []
        cons: list = []
        score = 50.0

        if not validation["ok"]:
            score -= 30
            cons.append(f"검증 오류: {validation['summary']}")
        if validation.get("warnings"):
            score -= 5 * len(validation["warnings"])
            cons.extend([f"경고: {w}" for w in validation["warnings"]])

        involved_companies = {mp.get("company_id") for mp in mission_plans}
        available_companies = set(blufor_units.keys())
        participation_ratio = len(involved_companies & available_companies) / max(len(available_companies), 1)
        score += participation_ratio * 10

        mission_types = {mp.get("mission_type") for mp in mission_plans}
        if "recon" in mission_types:
            pros.append("정찰 임무 포함 — 정보 우위 확보")
            score += 5
        if "attack" in mission_types or "flank" in mission_types:
            pros.append("공격 임무 포함 — 능동적 교전")
            score += 5
        if "flank" in mission_types:
            pros.append("측방 우회 기동 — 적 취약점 노출")
            score += 8
        if "defend" in mission_types or "hold" in mission_types:
            if len(mission_types) == 1:
                cons.append("방어 임무만 — 주도권 부재")
                score -= 5
            else:
                pros.append("방어 임무 병행 — 후방 안정")
        if "withdraw" in mission_types:
            cons.append("철수 임무 포함 — 전력 손실 우려")
            score -= 10

        detected_opfor = sum(1 for u in opfor_units if u.get("intel_status") == "detected")
        approx_opfor = sum(1 for u in opfor_units if u.get("intel_status") in ("approximate", "lost"))
        if approx_opfor > 0 and "recon" not in mission_types:
            cons.append(f"미탐지 OPFOR {approx_opfor}개 부대 — 정찰 없이 공격 시 위험")
            score -= 15
        elif approx_opfor > 0 and "recon" in mission_types:
            pros.append("정찰 임무로 미탐지 OPFOR 확인 예정")

        attack_companies = [mp for mp in mission_plans if mp.get("mission_type") in ("attack", "flank")]
        if opfor_units and len(attack_companies) > 0:
            ratio = len(attack_companies) / max(len(opfor_units), 1)
            if ratio >= 1.5:
                pros.append(f"공격 부대 우세 (비율 {ratio:.1f}:1)")
                score += 10
            elif ratio < 0.5:
                cons.append(f"공격 부대 열세 (비율 {ratio:.1f}:1) — 증원 권장")
                score -= 10

        score = max(0.0, min(100.0, score))

        if score >= 70:
            risk_level = "low"
        elif score >= 45:
            risk_level = "medium"
        else:
            risk_level = "high"

        evaluated.append({
            "coa_id": coa_id,
            "name": name,
            "score": round(score, 1),
            "pros": pros,
            "cons": cons,
            "risk_level": risk_level,
            "validation": validation,
            "recommended": False,
        })

    evaluated.sort(key=lambda x: x["score"], reverse=True)
    if evaluated:
        evaluated[0]["recommended"] = True
        recommended_coa = evaluated[0]["coa_id"]
    else:
        recommended_coa = ""

    summary_lines = [
        f"• {e['coa_id']} ({e['name']}): 점수 {e['score']}/100, 위험도 {e['risk_level']}"
        + (" ★ 권장" if e["recommended"] else "")
        for e in evaluated
    ]

    logger.info(f"COA 분석 완료: {len(evaluated)}개 방책 평가, 권장={recommended_coa}")
    return {
        "status": "success",
        "objective": objective,
        "evaluated": evaluated,
        "recommended_coa": recommended_coa,
        "summary": "\n".join(summary_lines),
    }
