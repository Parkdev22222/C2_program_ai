"""
LLM 기반 임무계획 생성기.

우선순위:
  1. Anthropic Claude API (ANTHROPIC_API_KEY 설정 시)
  2. vLLM 엔드포인트 (VLLM_BASE_URL 설정 시)
  3. 규칙 기반 폴백 (LLM 없을 때)

출력 JSON 형식:
{
  "reasoning": "전술 판단 설명",
  "mission_plans": [
    {
      "company_id": "Alpha",
      "mission_type": "attack",        # attack | defend | flank | withdraw | hold
      "waypoints": [[x1,y1],[x2,y2]],  # 순서대로 이동할 좌표 목록 (m)
      "objective": "Red1 중대 격멸"
    },
    ...
  ]
}
"""

import json
import logging
import os
import re
from typing import List, Optional

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 군사 작전 전문가 AI입니다. 주어진 전장 상황을 분석하고
각 중대별 최적 기동 경로를 포함한 임무계획을 JSON 형식으로 제시하세요.

규칙:
1. 좌표는 m 단위 (x=동쪽, y=북쪽, 지도 범위 0-30000)
2. 각 중대에 3-5개의 웨이포인트 제시
3. 화력 우위, 지형 이용, 상호지원을 고려
4. 전투력(CP) 30% 이하 부대는 방어/철수 우선
5. 반드시 유효한 JSON만 출력 (다른 텍스트 없음)

출력 형식:
{
  "reasoning": "판단 근거 (한국어 2-3문장)",
  "mission_plans": [
    {
      "company_id": "부대ID",
      "mission_type": "attack|defend|flank|withdraw|hold",
      "waypoints": [[x,y], ...],
      "objective": "임무 목표"
    }
  ]
}"""


def _build_state_prompt(state: dict) -> str:
    """현재 전장 상태를 LLM 프롬프트용 문자열로 변환."""
    lines = [
        f"게임 시간: {state.get('game_time_str', '00:00:00')}",
        "",
        "=== 현재 부대 상태 ===",
    ]
    for u in state.get("units", []):
        x_km = u["x"] / 1000
        y_km = u["y"] / 1000
        wps = len(u.get("waypoints", []))
        lines.append(
            f"[{u['side']}] {u['id']}: 위치=({x_km:.1f}km, {y_km:.1f}km) "
            f"고도={u.get('elevation',0):.0f}m "
            f"전투력={u['combat_power']:.0f}% "
            f"상태={u['status']} "
            f"잔여WP={wps}"
        )
    lines += [
        "",
        "=== 적 배치 분석 ===",
        _analyze_threats(state),
        "",
        "위 상황에서 BLUFOR 중대들의 최적 임무계획을 JSON으로 작성하세요.",
    ]
    return "\n".join(lines)


def _analyze_threats(state: dict) -> str:
    """간단한 위협 분석 문자열 생성."""
    blufor = [u for u in state["units"] if u["side"] == "BLUFOR" and u["status"] != "destroyed"]
    opfor  = [u for u in state["units"] if u["side"] == "OPFOR"  and u["status"] != "destroyed"]
    if not blufor or not opfor:
        return "적 전멸 또는 아군 전멸"

    bl_cx = sum(u["x"] for u in blufor) / len(blufor)
    bl_cy = sum(u["y"] for u in blufor) / len(blufor)
    op_cx = sum(u["x"] for u in opfor)  / len(opfor)
    op_cy = sum(u["y"] for u in opfor)  / len(opfor)
    dist  = ((bl_cx - op_cx)**2 + (bl_cy - op_cy)**2)**0.5

    return (
        f"BLUFOR 집결점: ({bl_cx/1000:.1f}km, {bl_cy/1000:.1f}km) | "
        f"OPFOR 집결점: ({op_cx/1000:.1f}km, {op_cy/1000:.1f}km) | "
        f"적과의 거리: {dist/1000:.1f}km"
    )


class MissionPlanner:
    """LLM 임무계획 생성기."""

    def __init__(self):
        self._mode = self._detect_mode()
        log.info(f"MissionPlanner 모드: {self._mode}")

    def _detect_mode(self) -> str:
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "claude"
        if os.environ.get("VLLM_BASE_URL"):
            return "vllm"
        return "rule"

    def plan(self, state: dict) -> dict:
        """전장 상태를 받아 임무계획 dict 반환."""
        prompt = _build_state_prompt(state)
        try:
            if self._mode == "claude":
                raw = self._call_claude(prompt)
            elif self._mode == "vllm":
                raw = self._call_vllm(prompt)
            else:
                return self._rule_based(state)

            result = self._parse_json(raw)
            if result:
                log.info("LLM 임무계획 생성 완료")
                return result
        except Exception as e:
            log.warning(f"LLM 호출 실패, 규칙 기반 폴백: {e}")

        return self._rule_based(state)

    # ── Claude API ─────────────────────────────────────────────────

    def _call_claude(self, prompt: str) -> str:
        import anthropic  # type: ignore
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    # ── vLLM API ──────────────────────────────────────────────────

    def _call_vllm(self, prompt: str) -> str:
        import requests
        base = os.environ["VLLM_BASE_URL"].rstrip("/")
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        resp = requests.post(
            f"{base}/v1/completions",
            json={
                "model": os.environ.get("VLLM_MODEL", "LGAI-EXAONE/EXAONE-4.0-7.8B-Instruct"),
                "prompt": full_prompt,
                "max_tokens": 2048,
                "temperature": 0.3,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["text"]

    # ── JSON 파싱 ──────────────────────────────────────────────────

    def _parse_json(self, text: str) -> Optional[dict]:
        text = text.strip()
        # 코드블록 제거
        text = re.sub(r"```(?:json)?", "", text).strip()
        # 첫 { ... } 추출
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None

    # ── 규칙 기반 폴백 ────────────────────────────────────────────

    def _rule_based(self, state: dict) -> dict:
        """LLM 없이 간단한 규칙으로 임무계획 생성."""
        units = {u["id"]: u for u in state["units"]}
        plans = []

        # OPFOR 생존 위치 파악
        opfor_alive = [u for u in state["units"]
                       if u["side"] == "OPFOR" and u["status"] != "destroyed"]

        if not opfor_alive:
            return {"reasoning": "모든 적 전멸.", "mission_plans": []}

        # OPFOR 평균 위치
        op_cx = sum(u["x"] for u in opfor_alive) / len(opfor_alive)
        op_cy = sum(u["y"] for u in opfor_alive) / len(opfor_alive)

        blufor = [u for u in state["units"]
                  if u["side"] == "BLUFOR" and u["status"] != "destroyed"]

        for i, u in enumerate(blufor):
            cp = u["combat_power"]
            if cp <= 5:
                continue

            if cp < 30:
                # 전투력 부족 → 현위치 방어
                plans.append({
                    "company_id": u["id"],
                    "mission_type": "defend",
                    "waypoints": [[u["x"], u["y"]]],
                    "objective": "현위치 방어"
                })
                continue

            # 접근 경로: 직선 + 약간 우회 (중간 지점 2개)
            mid1_x = u["x"] + (op_cx - u["x"]) * 0.35 + (400 if i % 2 == 0 else -400)
            mid1_y = u["y"] + (op_cy - u["y"]) * 0.35
            mid2_x = u["x"] + (op_cx - u["x"]) * 0.70
            mid2_y = u["y"] + (op_cy - u["y"]) * 0.70 + (300 if i % 2 == 0 else -300)

            plans.append({
                "company_id": u["id"],
                "mission_type": "attack",
                "waypoints": [
                    [round(mid1_x), round(mid1_y)],
                    [round(mid2_x), round(mid2_y)],
                    [round(op_cx + (300 if i % 2 == 0 else -300)), round(op_cy)],
                ],
                "objective": f"OPFOR 격멸 (목표 {op_cx/1000:.1f}km,{op_cy/1000:.1f}km)"
            })

        return {
            "reasoning": (
                f"규칙 기반 계획. OPFOR 집결점 ({op_cx/1000:.1f}km, {op_cy/1000:.1f}km) 공격."
            ),
            "mission_plans": plans,
        }
