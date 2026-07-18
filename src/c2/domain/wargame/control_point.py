"""통제구역(control point) 값 객체 — 순수 도메인.

점령·승리 판정 로직은 엔진(application)에 있고, 여기서는 위치 값 객체와
기본 배치만 정의한다. 표준 라이브러리만 의존한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ControlPoint:
    id: str
    x: float
    y: float


def default_control_points() -> List[ControlPoint]:
    """철원 시나리오 기본 통제구역 3곳 (경합지대)."""
    return [
        ControlPoint("통제-알파", 12_000.0, 14_000.0),
        ControlPoint("통제-브라보", 15_000.0, 15_000.0),
        ControlPoint("통제-찰리", 14_000.0, 12_000.0),
    ]
