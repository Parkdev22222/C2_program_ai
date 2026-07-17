"""Task 19: 엔진 순수 전투/탐지 계산 → c2.domain.wargame.combat 검증."""
import math
import re
from pathlib import Path

import pytest

from c2.domain.wargame import combat


# ── 순수 함수 값 고정 (원본 공식으로 손계산) ──────────────────────────────

def test_engagement_factor_tank_bands():
    ef = combat._engagement_factor
    # inner = 3000*0.4 = 1200; d_range=3000; s_range=5000
    assert ef("전차", 1000.0) == 1.0                      # dist <= inner
    assert ef("전차", 1200.0) == 1.0                      # dist == inner
    # 2000: 1.0 - (2000-1200)/(3000-1200)*0.5
    assert ef("전차", 2000.0) == pytest.approx(1.0 - 800.0 / 1800.0 * 0.5)
    # 4000: 0.5 - (4000-3000)/(5000-3000)*0.4 = 0.3
    assert ef("전차", 4000.0) == pytest.approx(0.3)
    assert ef("전차", 6000.0) == 0.0                      # beyond suppress range


def test_engagement_factor_spg_is_zero():
    assert combat._engagement_factor("자주포", 500.0) == 0.0


def test_engagement_factor_unknown_type_defaults():
    # unknown → d_range=2000, s_range=3000, inner=800
    ef = combat._engagement_factor("미상", 800.0)
    assert ef == 1.0


def test_matchup_factor():
    assert combat._matchup_factor("대전차", "전차") == 2.0
    assert combat._matchup_factor("자주포", "기계화보병") == 1.8
    assert combat._matchup_factor("정찰", "전차") == 0.2
    assert combat._matchup_factor("미상A", "미상B") == 1.0  # default


def test_status_firepower_mult():
    assert combat._status_firepower_mult("active") == 1.0
    assert combat._status_firepower_mult("degraded") == 0.8
    assert combat._status_firepower_mult("suppressed") == 0.3
    assert combat._status_firepower_mult("destroyed") == 0.0


def test_status_speed_mult():
    assert combat._status_speed_mult("active") == 1.0
    assert combat._status_speed_mult("degraded") == 0.7
    assert combat._status_speed_mult("suppressed") == 0.0
    assert combat._status_speed_mult("destroyed") == 0.0


def test_los_quality_same_point_is_open():
    # 동일 좌표: 중간 고도 == 보간 고도 → 완전 개방(1.0)
    q = combat._los_quality(5000.0, 5000.0, 5000.0, 5000.0)
    assert q == 1.0


def test_los_quality_in_unit_range():
    q = combat._los_quality(1000.0, 1000.0, 9000.0, 9000.0)
    assert 0.0 <= q <= 1.0


def test_constants_moved():
    assert combat._DIRECT_RANGE["전차"] == 3_000.0
    assert combat._SUPPRESS_RANGE["대전차"] == 4_500.0
    assert combat._MATCHUP[("대전차", "전차")] == 2.0


# ── combat.py 는 순수해야 한다 (외부/random/IO import 금지) ────────────────

def test_combat_module_is_pure():
    src = Path(combat.__file__).read_text(encoding="utf-8")
    forbidden = ["import random", "from random", "import wargame", "from wargame",
                 "import tools", "from tools", "import agent", "from agent",
                 "import ui", "from ui", "import logging"]
    for token in forbidden:
        assert token not in src, f"combat.py must not contain: {token}"
    # random 을 어떤 형태로도 참조하지 않는다
    assert not re.search(r"\brandom\b", src), "combat.py must not reference random"
