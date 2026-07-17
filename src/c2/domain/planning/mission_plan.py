"""임무계획 값 객체 — Pydantic typed schema + 순수 검증 로직.

구조:
- Waypoint, MissionPlanItem, AirSupportItem, MissionPlanRequest: typed schema
- validate_mission_plan(): 검증 로직 (error/warning 분리)

순수 도메인 모듈. pydantic(값 객체 검증 라이브러리) 외 프레임워크/인프라 의존성 없음.

VALID_COMPANY_IDS는 시나리오에 따라 갱신되는 가변 allow-list다.
갱신은 `tools.mission_plan_validator.update_valid_company_ids()`가 이 모듈의
속성을 직접 재할당하는 방식으로 수행한다 (tools → domain 방향 의존이므로
계층 규칙 위반이 아니다). validate_mission_plan()과 Pydantic validator들은
모듈 전역을 호출 시점에 조회하므로 갱신이 즉시 반영된다.
"""
import logging
from typing import List, Optional, Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 맵 상수 / allow-list
# ─────────────────────────────────────────────
MAP_MAX = 30_000.0
VALID_COMPANY_IDS = {"Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"}
VALID_MISSION_TYPES = {"recon", "attack", "defend", "flank", "withdraw", "hold"}
VALID_SUPPORT_TYPES = {"cas", "strike", "artillery", "helicopter"}

# ─────────────────────────────────────────────
# Pydantic 기반 typed schema (v1/v2 호환)
# ─────────────────────────────────────────────
try:
    from pydantic import BaseModel, Field, validator

    class Waypoint(BaseModel):
        """위경도(WGS84) 또는 내부 미터 좌표를 모두 허용하는 경유지 모델.
        lat/lon 형식: lat=-90~90, lon=-180~180 (소수점 값)
        미터 형식:    x=0~30000, y=0~30000 (정수 또는 소수)
        """
        x: float = Field(ge=-180, le=MAP_MAX)   # lon 또는 x_m 모두 허용
        y: float = Field(ge=-90, le=MAP_MAX)    # lat 또는 y_m 모두 허용

    class MissionPlanItem(BaseModel):
        company_id: str
        mission_type: str
        waypoints: List[Waypoint]
        objective: str
        target_unit_id: Optional[str] = None   # 이 부대가 담당·추격할 적 부대 ID

        @validator("company_id")
        def _check_company_id(cls, v):
            if v not in VALID_COMPANY_IDS:
                raise ValueError(f"company_id '{v}'는 허용 부대({VALID_COMPANY_IDS})가 아닙니다.")
            return v

        @validator("mission_type")
        def _check_mission_type(cls, v):
            if v not in VALID_MISSION_TYPES:
                raise ValueError(f"mission_type '{v}'는 허용 값({VALID_MISSION_TYPES})이 아닙니다.")
            return v

        @validator("waypoints", pre=True)
        def _coerce_waypoints(cls, v):
            # [lat/lon 또는 x, y] 리스트 형식을 {"x": x, "y": y} 딕셔너리로 자동 변환
            coerced = []
            for wp in v:
                if isinstance(wp, (list, tuple)) and len(wp) == 2:
                    coerced.append({"x": wp[0], "y": wp[1]})
                else:
                    coerced.append(wp)
            if not coerced:
                raise ValueError("waypoints는 최소 1개 이상이어야 합니다.")
            return coerced

    class AirSupportItem(BaseModel):
        call_sign: str
        support_type: str
        target: List[float]
        radius: float = Field(gt=0, le=10_000)
        delay: float = Field(ge=0, le=3600)

        @validator("support_type")
        def _check_support_type(cls, v):
            if v not in VALID_SUPPORT_TYPES:
                raise ValueError(f"support_type '{v}'는 허용 값({VALID_SUPPORT_TYPES})이 아닙니다.")
            return v

        @validator("target")
        def _check_target(cls, v):
            if len(v) != 2:
                raise ValueError("target은 [lat, lon] 또는 [x, y] 2개 좌표여야 합니다.")
            # lat/lon 또는 미터 좌표 모두 허용 (변환은 apply 단계에서 수행)
            return v

    class MissionPlanRequest(BaseModel):
        plan_id: Optional[str] = None
        mission_plans: List[MissionPlanItem]
        air_support_plans: List[AirSupportItem] = []
        dry_run: bool = True

        @validator("mission_plans")
        def _check_not_empty(cls, v):
            if not v:
                raise ValueError("mission_plans는 비어 있을 수 없습니다.")
            return v

    _PYDANTIC_OK = True
except ImportError:
    _PYDANTIC_OK = False
    logger.warning("pydantic 미설치 — typed schema 검증 비활성화, 기본 dict 검증만 수행합니다.")


# ─────────────────────────────────────────────
# validate_mission_plan
# ─────────────────────────────────────────────

def validate_mission_plan(plan: Any) -> dict:
    """
    임무계획을 검증합니다.

    Returns:
        {
          "ok": bool,
          "errors": list[str],
          "warnings": list[str],
          "summary": str
        }
    """
    errors: List[str] = []
    warnings: List[str] = []

    if isinstance(plan, str):
        import json as _json
        try:
            plan = _json.loads(plan)
        except Exception as e:
            return {"ok": False, "errors": [f"JSON 파싱 실패: {e}"], "warnings": [], "summary": "파싱 오류"}

    mission_plans = plan.get("mission_plans", [])
    air_support_plans = plan.get("air_support_plans", [])

    if not mission_plans:
        errors.append("mission_plans가 비어 있습니다.")

    if _PYDANTIC_OK and mission_plans:
        try:
            MissionPlanRequest(
                plan_id=plan.get("plan_id"),
                mission_plans=mission_plans,
                air_support_plans=air_support_plans,
            )
        except Exception as e:
            errors.append(f"Schema 검증 실패: {e}")

    seen_companies: set = set()
    has_recon = False
    has_attack = False

    for mp in mission_plans:
        cid = mp.get("company_id", "")
        mtype = mp.get("mission_type", "")
        wps = mp.get("waypoints", [])

        if cid not in VALID_COMPANY_IDS:
            errors.append(f"허용되지 않은 company_id: '{cid}'")

        if mtype not in VALID_MISSION_TYPES:
            errors.append(f"허용되지 않은 mission_type: '{mtype}' (부대: {cid})")

        if cid in seen_companies:
            warnings.append(f"동일 부대({cid})에 중복 임무가 있습니다.")
        seen_companies.add(cid)

        for i, wp in enumerate(wps):
            if isinstance(wp, (list, tuple)) and len(wp) == 2:
                x, y = wp
            elif isinstance(wp, dict):
                x, y = wp.get("x", wp.get("lon", 0)), wp.get("y", wp.get("lat", 0))
            else:
                errors.append(f"{cid} waypoint[{i}] 형식 오류: {wp}")
                continue
            fx, fy = float(x), float(y)
            # 위경도 형식 (lat: -90~90 소수, lon: -180~180 소수) 또는 미터 형식 (0~30000) 허용
            is_latlon = (-90.0 <= fy <= 90.0 and -180.0 <= fx <= 180.0
                         and (fy != round(fy) or fx != round(fx)))
            if not is_latlon and not (0 <= fx <= MAP_MAX and 0 <= fy <= MAP_MAX):
                errors.append(f"{cid} waypoint[{i}] 좌표 범위 초과: ({x}, {y})")

        if mtype == "recon":
            has_recon = True
        if mtype in ("attack", "flank"):
            has_attack = True

    if has_recon and has_attack:
        warnings.append("정찰 임무와 공격 임무가 혼재합니다. 정찰 완료 후 공격 권장.")

    for asp in air_support_plans:
        stype = asp.get("support_type", "")
        target = asp.get("target", [])
        radius = asp.get("radius", 0)
        delay = asp.get("delay", 0)

        if stype not in VALID_SUPPORT_TYPES:
            errors.append(f"허용되지 않은 support_type: '{stype}'")

        if len(target) != 2:
            errors.append(f"공중지원 target 형식 오류: {target}")
        else:
            tx, ty = target
            ftx, fty = float(tx), float(ty)
            # 위경도 형식 또는 미터 형식 모두 허용 (변환은 apply 단계에서 수행)
            is_latlon_tgt = (-90.0 <= fty <= 90.0 and -180.0 <= ftx <= 180.0
                             and (fty != round(fty) or ftx != round(ftx)))
            if not is_latlon_tgt and not (0 <= ftx <= MAP_MAX and 0 <= fty <= MAP_MAX):
                errors.append(f"공중지원 target 좌표 범위 초과: {target}")

        if radius <= 0 or radius > 10_000:
            warnings.append(f"공중지원 radius 비정상: {radius}m")

        if delay < 0 or delay > 3600:
            warnings.append(f"공중지원 delay 비정상: {delay}s")

    ok = len(errors) == 0
    summary_parts = []
    if errors:
        summary_parts.append(f"오류 {len(errors)}건")
    if warnings:
        summary_parts.append(f"경고 {len(warnings)}건")
    summary = "검증 통과" if ok and not warnings else (", ".join(summary_parts) if summary_parts else "통과")

    return {"ok": ok, "errors": errors, "warnings": warnings, "summary": summary}
