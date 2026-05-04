"""
워게임 지형 모델.

- 30km x 30km 격자 (100m 해상도 = 300x300)
- numpy 기반 고도 행렬 (0-400m)
- 고도, 엄폐, 이동속도 계수 제공
"""

import numpy as np
from functools import lru_cache
from typing import Tuple

# 지도 크기 (m)
MAP_W = 30_000
MAP_H = 30_000
GRID_RES = 100      # m per cell
GRID_W = MAP_W // GRID_RES   # 300
GRID_H = MAP_H // GRID_RES   # 300


def _generate_heightmap(seed: int = 42) -> np.ndarray:
    """
    Altis 지형을 모사한 고도 행렬 생성.

    남부(y<8km): 평지 (BLUFOR 집결지)
    중부(8-22km): 구릉지 (교전 지역)
    북부(y>22km): 구릉 평지 (OPFOR 집결지)
    """
    rng = np.random.default_rng(seed)
    h = np.zeros((GRID_H, GRID_W), dtype=np.float32)

    xv, yv = np.meshgrid(np.arange(GRID_W), np.arange(GRID_H))

    # 주요 능선 및 구릉 (교전 지역 중부에 집중)
    hills = [
        # (cx, cy, height, sigma_x, sigma_y) — 격자 단위
        (120, 130, 280, 25, 20),
        (80,  150, 200, 18, 15),
        (180, 140, 240, 22, 18),
        (150, 110, 180, 15, 20),
        (100, 180, 160, 20, 12),
        (200, 160, 190, 16, 22),
        (60,  120, 150, 14, 14),
        (240, 130, 170, 18, 16),
        (130, 200, 140, 20, 18),
        (170, 90,  120, 16, 16),
        # 소규모 구릉
        (50,  100, 80, 10, 10),
        (220, 100, 90, 12, 10),
        (140, 155, 200, 30, 10),  # 중앙 능선
        (70,  170, 110, 10, 14),
        (190, 175, 130, 12, 10),
    ]
    for cx, cy, ht, sx, sy in hills:
        h += ht * np.exp(
            -(((xv - cx) ** 2) / (2 * sx ** 2) + ((yv - cy) ** 2) / (2 * sy ** 2))
        )

    # 노이즈 추가
    noise = rng.uniform(-20, 20, (GRID_H, GRID_W)).astype(np.float32)
    h += noise

    # 스무딩 (scipy 없이 간단 박스필터)
    kernel_size = 5
    pad = kernel_size // 2
    h_pad = np.pad(h, pad, mode="edge")
    h_smooth = np.zeros_like(h)
    for dy in range(kernel_size):
        for dx in range(kernel_size):
            h_smooth += h_pad[dy:dy+GRID_H, dx:dx+GRID_W]
    h = h_smooth / (kernel_size ** 2)

    # 남북 평지 마스크: 남부(y<80) 및 북부(y>220)는 낮게 유지
    south_mask = np.maximum(0, (80 - yv) / 80)   # 0→1 as y→0
    north_mask = np.maximum(0, (yv - 220) / 80)  # 0→1 as y→300
    h *= (1 - south_mask * 0.85)
    h *= (1 - north_mask * 0.7)

    return np.clip(h, 0, 400).astype(np.float32)


# 전역 싱글턴 지형
_heightmap: np.ndarray = None

def get_heightmap() -> np.ndarray:
    global _heightmap
    if _heightmap is None:
        _heightmap = _generate_heightmap()
    return _heightmap


class Terrain:
    """고도/엄폐/이동속도 조회 인터페이스."""

    def __init__(self):
        self._h = get_heightmap()

    def _cell(self, x: float, y: float) -> Tuple[int, int]:
        """좌표 (m) → 격자 인덱스 (col, row)."""
        col = int(np.clip(x / GRID_RES, 0, GRID_W - 1))
        row = int(np.clip(y / GRID_RES, 0, GRID_H - 1))
        return col, row

    def elevation(self, x: float, y: float) -> float:
        """해당 위치의 고도 (m)."""
        col, row = self._cell(x, y)
        return float(self._h[row, col])

    def elevation_advantage(self, attacker_x: float, attacker_y: float,
                            defender_x: float, defender_y: float) -> float:
        """
        공격자의 고도 우위 계수.
        +1.3 (고지 공격) ~ 0.8 (저지 공격).
        """
        elev_a = self.elevation(attacker_x, attacker_y)
        elev_d = self.elevation(defender_x, defender_y)
        diff = elev_a - elev_d
        if diff > 80:
            return 1.3
        elif diff > 30:
            return 1.15
        elif diff < -80:
            return 0.8
        elif diff < -30:
            return 0.9
        return 1.0

    def cover_factor(self, x: float, y: float) -> float:
        """
        방어자 엄폐 계수 (0=무방호 ~ 0.6=최대 엄폐).
        고지대일수록, 경사가 급할수록 엄폐 증가.
        """
        col, row = self._cell(x, y)
        h = self._h
        # 경사 계산 (주변 4방향 평균 고도차)
        r0 = max(0, row - 1)
        r1 = min(GRID_H - 1, row + 1)
        c0 = max(0, col - 1)
        c1 = min(GRID_W - 1, col + 1)
        slope = (abs(float(h[r1, col]) - float(h[r0, col])) +
                 abs(float(h[row, c1]) - float(h[row, c0]))) / 2
        elev = float(h[row, col])
        cover = min(0.55, slope * 0.01 + elev * 0.001)
        return cover

    def movement_speed_factor(self, x: float, y: float) -> float:
        """
        지형 기반 이동 속도 계수 (0.3=험지 ~ 1.0=평지).
        """
        col, row = self._cell(x, y)
        h = self._h
        r0 = max(0, row - 1)
        r1 = min(GRID_H - 1, row + 1)
        c0 = max(0, col - 1)
        c1 = min(GRID_W - 1, col + 1)
        slope = (abs(float(h[r1, col]) - float(h[r0, col])) +
                 abs(float(h[row, c1]) - float(h[row, c0]))) / 2
        factor = max(0.3, 1.0 - slope * 0.015)
        return factor


# 전역 인스턴스
terrain = Terrain()
