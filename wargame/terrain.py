"""
워게임 지형 모델 — 실제 대한민국 DEM 데이터 (AWS Terrarium / SRTM 30m)

작전 지역: 철원 지역 (DMZ 인근, lat 38.0~38.27, lon 127.0~127.34)

실제 데이터 로드 실패 시 합성 지형으로 폴백.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

MAP_W    = 30_000
MAP_H    = 30_000
GRID_RES = 100
GRID_W   = MAP_W // GRID_RES   # 300
GRID_H   = MAP_H // GRID_RES   # 300


def get_heightmap() -> np.ndarray:
    """실제 DEM 또는 합성 고도맵 반환 (300×300)."""
    return terrain.get_heightmap()


# ── 실제 지형 로드 ─────────────────────────────────────────────────────
def _load_terrain():
    try:
        from wargame.terrain_korea import KoreaRealTerrain
        t = KoreaRealTerrain()
        logger.info("[지형] 실제 한국 DEM 로드 완료 (철원 지역)")
        return t
    except Exception as e:
        logger.warning(f"[지형] 실제 DEM 로드 실패: {e} — 합성 지형 사용")
        return _FallbackTerrain()


# ── 폴백: 기존 합성 지형 ──────────────────────────────────────────────
from typing import Tuple

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

def _generate_fallback_heightmap(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    xv, yv = np.meshgrid(
        np.linspace(0, 1, GRID_W, dtype=np.float32),
        np.linspace(0, 1, GRID_H, dtype=np.float32),
    )
    octaves = [(6,260),(12,130),(24,55),(48,22),(96,9),(180,4),(256,1.5)]
    h = np.zeros((GRID_H, GRID_W), dtype=np.float32)
    for gdiv, amp in octaves:
        small = rng.uniform(0, 1, (gdiv, gdiv)).astype(np.float32)
        h += _bilinear_upsample(small, GRID_H, GRID_W) * amp
    ridge_a = np.exp(-np.abs(yv - xv - 0.05)**2 / (2*0.06**2)) * 200
    ridge_b = np.exp(-np.abs(yv - 0.68)**2 / (2*0.04**2)) * 160
    valley  = -np.exp(-np.abs(yv - 0.43)**2 / (2*0.05**2)) * 220
    river   = -np.exp(-np.abs(yv - 0.43 - 0.04*np.sin(xv*12))**2 / (2*0.015**2)) * 100
    plateau = (np.clip((xv-0.60)/0.12,0,1)*np.clip((yv-0.62)/0.12,0,1)*
               np.clip((0.95-xv)/0.10,0,1)*np.clip((0.90-yv)/0.10,0,1)) * 180
    basin_r = np.sqrt((xv-0.28)**2+(yv-0.22)**2)
    basin   = -np.exp(-basin_r**2/(2*0.12**2)) * 160
    h = _box_smooth(h + ridge_a + ridge_b + valley + river + plateau + basin, k=5)
    h = (h - h.min()) / (h.max() - h.min()) * 500
    return h.astype(np.float32)

class _FallbackTerrain:
    def __init__(self):
        self._h = _generate_fallback_heightmap()
    def _cell(self, x, y):
        col = int(np.clip(x / GRID_RES, 0, GRID_W - 1))
        row = int(np.clip(y / GRID_RES, 0, GRID_H - 1))
        return col, row
    def elevation(self, x, y):
        col, row = self._cell(x, y)
        return float(self._h[row, col])
    def elevation_advantage(self, ax, ay, dx, dy):
        diff = self.elevation(ax, ay) - self.elevation(dx, dy)
        if diff > 120: return 1.40
        if diff >  60: return 1.25
        if diff >  20: return 1.10
        if diff < -120: return 0.75
        if diff <  -60: return 0.85
        if diff <  -20: return 0.93
        return 1.00
    def cover_factor(self, x, y):
        col, row = self._cell(x, y)
        h = self._h
        r0=max(0,row-2); r1=min(GRID_H-1,row+2)
        c0=max(0,col-2); c1=min(GRID_W-1,col+2)
        slope=(abs(float(h[r1,col])-float(h[r0,col]))+abs(float(h[row,c1])-float(h[row,c0])))/2
        return float(np.clip(slope*0.008+float(h[row,col])*0.0008, 0.0, 0.65))
    def movement_speed_factor(self, x, y):
        col, row = self._cell(x, y)
        h = self._h
        r0=max(0,row-1); r1=min(GRID_H-1,row+1)
        c0=max(0,col-1); c1=min(GRID_W-1,col+1)
        slope=(abs(float(h[r1,col])-float(h[r0,col]))+abs(float(h[row,c1])-float(h[row,c0])))/2
        return float(np.clip(1.0-slope*0.012, 0.25, 1.0))
    def get_heightmap(self):
        return self._h


# 전역 인스턴스
terrain = _load_terrain()


