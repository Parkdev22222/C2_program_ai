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
    """임의 고도 행렬 생성 (프랙탈 노이즈 합성)."""
    rng = np.random.default_rng(seed)

    h = np.zeros((GRID_H, GRID_W), dtype=np.float32)

    # 옥타브 노이즈: 여러 해상도 랜덤 레이어를 합산
    octaves = [
        (8,  200.0),   # 대규모 산맥
        (16,  80.0),   # 중규모 구릉
        (32,  30.0),   # 소규모 언덕
        (64,  12.0),   # 세부 지형
        (128,  5.0),   # 미세 굴곡
    ]
    for grid_div, amplitude in octaves:
        # 작은 격자에 랜덤 값 생성 후 업샘플
        small_h = rng.uniform(0, 1, (grid_div, grid_div)).astype(np.float32)
        # 선형 보간 업샘플
        from_h = np.linspace(0, grid_div - 1, GRID_H)
        from_w = np.linspace(0, grid_div - 1, GRID_W)
        ih = np.floor(from_h).astype(int).clip(0, grid_div - 2)
        iw = np.floor(from_w).astype(int).clip(0, grid_div - 2)
        fh = (from_h - ih)[:, None]
        fw = (from_w - iw)[None, :]
        upsampled = (
            small_h[ih[:, None], iw[None, :]] * (1 - fh) * (1 - fw)
            + small_h[(ih+1)[:, None], iw[None, :]] * fh * (1 - fw)
            + small_h[ih[:, None], (iw+1)[None, :]] * (1 - fh) * fw
            + small_h[(ih+1)[:, None], (iw+1)[None, :]] * fh * fw
        )
        h += upsampled * amplitude

    # 박스 스무딩 (scipy 없이)
    k = 7
    pad = k // 2
    h_pad = np.pad(h, pad, mode="edge")
    h_s = np.zeros_like(h)
    for dy in range(k):
        for dx in range(k):
            h_s += h_pad[dy:dy+GRID_H, dx:dx+GRID_W]
    h = h_s / (k * k)

    # 0~400m 범위로 정규화
    h = (h - h.min()) / (h.max() - h.min()) * 400

    return h.astype(np.float32)


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
