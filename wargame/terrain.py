"""
워게임 지형 모델 — 복합 지형 생성기.

30km × 30km, 100m 해상도 (300×300 격자)

지형 구성 요소:
  1. 프랙탈 노이즈 (7 옥타브) — 자연스러운 기복
  2. 능선 (Ridge) — 대각선 방향 고지대 2개
  3. 계곡 (Valley) — 지도 중앙 동서 방향 저지대 회랑
  4. 고원 (Plateau) — 북동부 고원 지대
  5. 분지 (Basin) — 남서부 아군 집결 평지
  6. 하천부지 (River) — 계곡 내 좁은 최저점 (이동 불리)

고도 범위: 0 ~ 500m
"""

import numpy as np
from typing import Tuple

MAP_W    = 30_000
MAP_H    = 30_000
GRID_RES = 100
GRID_W   = MAP_W // GRID_RES   # 300
GRID_H   = MAP_H // GRID_RES   # 300


# ── 보간 업샘플 ────────────────────────────────────────────────────

def _bilinear_upsample(small: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    sh, sw = small.shape
    fh = np.linspace(0, sh - 1, out_h)
    fw = np.linspace(0, sw - 1, out_w)
    ih = np.floor(fh).astype(int).clip(0, sh - 2)
    iw = np.floor(fw).astype(int).clip(0, sw - 2)
    tfh = (fh - ih)[:, None]
    tfw = (fw - iw)[None, :]
    return (
        small[ih[:, None], iw[None, :]] * (1 - tfh) * (1 - tfw)
        + small[(ih+1)[:, None], iw[None, :]] * tfh * (1 - tfw)
        + small[ih[:, None], (iw+1)[None, :]] * (1 - tfh) * tfw
        + small[(ih+1)[:, None], (iw+1)[None, :]] * tfh * tfw
    )


def _box_smooth(h: np.ndarray, k: int = 5) -> np.ndarray:
    pad = k // 2
    hp = np.pad(h, pad, mode="edge")
    out = np.zeros_like(h)
    for dy in range(k):
        for dx in range(k):
            out += hp[dy:dy+GRID_H, dx:dx+GRID_W]
    return out / (k * k)


# ── 지형 생성 ──────────────────────────────────────────────────────

def _generate_heightmap(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    xv, yv = np.meshgrid(
        np.linspace(0, 1, GRID_W, dtype=np.float32),
        np.linspace(0, 1, GRID_H, dtype=np.float32),
    )

    # ── 1. 프랙탈 노이즈 (7 옥타브) ─────────────────────────────
    octaves = [
        (6,   260.0),  # 대규모 산맥
        (12,  130.0),  # 중규모 산줄기
        (24,   55.0),  # 소규모 구릉
        (48,   22.0),  # 세부 지형
        (96,    9.0),  # 미세 굴곡
        (180,   4.0),  # 암반 질감
        (256,   1.5),  # 초미세 노이즈
    ]
    h = np.zeros((GRID_H, GRID_W), dtype=np.float32)
    for gdiv, amp in octaves:
        small = rng.uniform(0, 1, (gdiv, gdiv)).astype(np.float32)
        h += _bilinear_upsample(small, GRID_H, GRID_W) * amp

    # ── 2. 대각 능선 A (남서→북동, 지도 중앙 통과) ──────────────
    # 능선 방정식: y = x + offset  (격자 좌표 0~1)
    ridge_a_dist = np.abs(yv - xv - 0.05)          # 거리
    ridge_a = np.exp(-ridge_a_dist**2 / (2 * 0.06**2)) * 200

    # ── 3. 능선 B (서→동, 북쪽 1/3 지점) ───────────────────────
    ridge_b_dist = np.abs(yv - 0.68)
    ridge_b = np.exp(-ridge_b_dist**2 / (2 * 0.04**2)) * 160

    # ── 4. 동서 계곡 (지도 중앙 y≈0.43) — 교전 저지대 회랑 ─────
    valley_dist = np.abs(yv - 0.43)
    valley = -np.exp(-valley_dist**2 / (2 * 0.05**2)) * 220

    # ── 5. 하천 (계곡 중앙 최저점, x 방향으로 약간 사행) ─────────
    river_cx = 0.43 + 0.04 * np.sin(xv * 12)
    river_dist = np.abs(yv - river_cx)
    river = -np.exp(-river_dist**2 / (2 * 0.015**2)) * 100

    # ── 6. 북동부 고원 ───────────────────────────────────────────
    plateau_mask = (
        np.clip((xv - 0.60) / 0.12, 0, 1) *
        np.clip((yv - 0.62) / 0.12, 0, 1) *
        np.clip((0.95 - xv) / 0.10, 0, 1) *
        np.clip((0.90 - yv) / 0.10, 0, 1)
    )
    plateau = plateau_mask * 180

    # ── 7. 남서 평지 분지 (BLUFOR 집결지) ───────────────────────
    basin_cx, basin_cy = 0.28, 0.22
    basin_r = np.sqrt((xv - basin_cx)**2 + (yv - basin_cy)**2)
    basin = -np.exp(-basin_r**2 / (2 * 0.12**2)) * 160

    # ── 합산 ─────────────────────────────────────────────────────
    h = h + ridge_a + ridge_b + valley + river + plateau + basin

    # ── 스무딩 ───────────────────────────────────────────────────
    h = _box_smooth(h, k=5)

    # ── 0~500m 정규화 ────────────────────────────────────────────
    h = (h - h.min()) / (h.max() - h.min()) * 500

    return h.astype(np.float32)


# ── 전역 싱글턴 ───────────────────────────────────────────────────

_heightmap: np.ndarray = None


def get_heightmap() -> np.ndarray:
    global _heightmap
    if _heightmap is None:
        _heightmap = _generate_heightmap()
    return _heightmap


# ── Terrain 클래스 ────────────────────────────────────────────────

class Terrain:
    """고도·엄폐·이동속도 조회 인터페이스."""

    def __init__(self):
        self._h = get_heightmap()

    def _cell(self, x: float, y: float) -> Tuple[int, int]:
        col = int(np.clip(x / GRID_RES, 0, GRID_W - 1))
        row = int(np.clip(y / GRID_RES, 0, GRID_H - 1))
        return col, row

    def elevation(self, x: float, y: float) -> float:
        col, row = self._cell(x, y)
        return float(self._h[row, col])

    def elevation_advantage(self, ax: float, ay: float,
                            dx: float, dy: float) -> float:
        """공격자 고도 우위 계수 (0.75 ~ 1.40)."""
        diff = self.elevation(ax, ay) - self.elevation(dx, dy)
        if diff > 120:  return 1.40
        if diff >  60:  return 1.25
        if diff >  20:  return 1.10
        if diff < -120: return 0.75
        if diff <  -60: return 0.85
        if diff <  -20: return 0.93
        return 1.00

    def cover_factor(self, x: float, y: float) -> float:
        """방어 엄폐 계수 (0.0 ~ 0.65)."""
        col, row = self._cell(x, y)
        h = self._h
        r0 = max(0, row - 2); r1 = min(GRID_H - 1, row + 2)
        c0 = max(0, col - 2); c1 = min(GRID_W - 1, col + 2)
        slope = (abs(float(h[r1, col]) - float(h[r0, col])) +
                 abs(float(h[row, c1]) - float(h[row, c0]))) / 2
        elev  = float(h[row, col])
        return float(np.clip(slope * 0.008 + elev * 0.0008, 0.0, 0.65))

    def movement_speed_factor(self, x: float, y: float) -> float:
        """이동 속도 계수 (0.25 ~ 1.0)."""
        col, row = self._cell(x, y)
        h = self._h
        r0 = max(0, row - 1); r1 = min(GRID_H - 1, row + 1)
        c0 = max(0, col - 1); c1 = min(GRID_W - 1, col + 1)
        slope = (abs(float(h[r1, col]) - float(h[r0, col])) +
                 abs(float(h[row, c1]) - float(h[row, c0]))) / 2
        return float(np.clip(1.0 - slope * 0.012, 0.25, 1.0))


# 전역 인스턴스
terrain = Terrain()
