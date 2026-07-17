"""
워게임 지형 모델 — 실제 대한민국 DEM 데이터 (AWS Terrarium / SRTM 30m)

작전 지역: 철원 지역 (DMZ 인근, lat 38.0~38.27, lon 127.0~127.34)

실제 데이터 로드 실패 시 합성 지형으로 폴백.

원래 wargame/terrain.py + wargame/terrain_korea.py 두 모듈이었던 것을
순수 도메인 계층(numpy 만 사용, 프레임워크/tools/agent/ontology/ui 의존 없음)으로
통합 이동. 원본 위치는 이 모듈을 재-export 하는 shim 으로 대체됨.
"""

import io
import json
import logging
import math
import os
import urllib.request
from pathlib import Path
from typing import Tuple

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


# =========================================================================
# ── 실제 한국 지형 모델 (구 wargame/terrain_korea.py) ──────────────────
# =========================================================================
#
# 실제 대한민국 지형 모델 — AWS Terrarium DEM (SRTM 30m) 기반
#
# 작전 지역: 철원 지역 (DMZ 인근)
#   - 기준점: lat=38.0, lon=127.0
#   - 범위: 30km × 30km (lat 38.0~38.27, lon 127.0~127.34)
#   - 해상도: zoom 12 (~30m/pixel), 300×300 격자로 리샘플
#
# 엔진 내부 좌표계: x=동쪽(m), y=북쪽(m), 범위 0~30000

# ── 작전 지역 상수 ────────────────────────────────────────────────────
REF_LAT  = 38.0
REF_LON  = 127.0
MAP_W_M  = 30_000
MAP_H_M  = 30_000
# GRID_RES / GRID_W / GRID_H 는 위 상단에서 이미 정의됨 (동일 값)

METERS_PER_DEG_LAT = 111_000.0
METERS_PER_DEG_LON = 111_000.0 * math.cos(math.radians(REF_LAT))

# ── 타일 파라미터 ─────────────────────────────────────────────────────
ZOOM    = 12
TX_MIN  = 3492
TX_MAX  = 3496
TY_MIN  = 1576
TY_MAX  = 1579

# 데이터 디렉터리: 저장소 루트의 data/ (이 파일 위치와 무관하게 절대 경로로 해석)
# src/c2/domain/wargame/terrain.py 기준 repo root 는 parents[4]
# (terrain.py -> wargame -> domain -> c2 -> src -> <repo root>)
_REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR  = _REPO_ROOT / "data"
DEM_FILE  = DATA_DIR / "korea_dem_cheorwon.npy"
META_FILE = DATA_DIR / "korea_dem_meta.json"

# 모자이크 원점 (타일 그리드 북서 코너)
_MOSAIC_LAT_N = 38.272689
_MOSAIC_LON_W = 126.914062

# crop 파라미터 (모자이크 → 30km×30km)
_ROW_TOP   = 8
_ROW_BOT   = 1009
_COL_LEFT  = 250
_COL_RIGHT = 1249


# ── 헬퍼 ──────────────────────────────────────────────────────────────

def _latlon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    lat_r = math.radians(lat)
    n = 2 ** zoom
    tx = int((lon + 180) / 360 * n)
    ty = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return tx, ty


def _fetch_tile(z: int, tx: int, ty: int) -> np.ndarray:
    from PIL import Image

    url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{tx}/{ty}.png"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB")
    arr = np.array(img, dtype=np.float32)
    return arr[:, :, 0] * 256 + arr[:, :, 1] + arr[:, :, 2] / 256 - 32768


def _download_mosaic() -> np.ndarray:
    """타일 다운로드 → 모자이크 합성."""
    logger.info(f"[DEM] 철원 지역 DEM 타일 다운로드 시작 ({(TX_MAX-TX_MIN+1)*(TY_MAX-TY_MIN+1)}개)...")
    rows = []
    for ty in range(TY_MIN, TY_MAX + 1):
        cols = []
        for tx in range(TX_MIN, TX_MAX + 1):
            tile = _fetch_tile(ZOOM, tx, ty)
            cols.append(tile)
            logger.debug(f"  tile ({tx},{ty}): {tile.min():.0f}~{tile.max():.0f}m")
        rows.append(np.hstack(cols))
    return np.vstack(rows)


def _load_or_download_dem() -> np.ndarray:
    """캐시 파일이 있으면 로드, 없으면 다운로드 후 저장."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DEM_FILE.exists():
        logger.info(f"[DEM] 캐시 로드: {DEM_FILE}")
        return np.load(str(DEM_FILE))
    logger.info("[DEM] 캐시 없음 — AWS에서 타일 다운로드")
    mosaic = _download_mosaic()
    np.save(str(DEM_FILE), mosaic)
    logger.info(f"[DEM] 저장 완료: {DEM_FILE}")
    return mosaic


def _build_heightmap() -> np.ndarray:
    """30km×30km 영역 crop → 300×300 격자 리샘플."""
    from PIL import Image

    mosaic = _load_or_download_dem()
    cropped = mosaic[_ROW_TOP:_ROW_BOT, _COL_LEFT:_COL_RIGHT]

    # scipy로 리샘플 (없으면 PIL 사용)
    try:
        from scipy.ndimage import zoom as sp_zoom
        scale_r = GRID_H / cropped.shape[0]
        scale_c = GRID_W / cropped.shape[1]
        resampled = sp_zoom(cropped, (scale_r, scale_c), order=1)
    except ImportError:
        img = Image.fromarray(cropped.astype(np.float32), mode="F")
        img = img.resize((GRID_W, GRID_H), Image.BILINEAR)
        resampled = np.array(img, dtype=np.float32)

    # 음수 고도(해수면 이하) 0으로 클램프
    resampled = np.clip(resampled, 0, None)

    logger.info(
        f"[DEM] 지형 준비 완료 — 철원 지역 {GRID_H}×{GRID_W} "
        f"고도 범위: {resampled.min():.0f}~{resampled.max():.0f}m "
        f"평균: {resampled.mean():.0f}m"
    )
    return resampled.astype(np.float32)


class KoreaRealTerrain:
    """
    실제 대한민국 SRTM 고도 기반 지형 모델.
    기존 Terrain 클래스와 동일한 인터페이스 제공.
    """

    def __init__(self):
        self._h = _build_heightmap()

    def _cell(self, x: float, y: float) -> Tuple[int, int]:
        # y=0 → 남쪽(row GRID_H-1), y=MAP_H_M → 북쪽(row 0)
        col = int(np.clip(x / GRID_RES, 0, GRID_W - 1))
        row = int(np.clip((MAP_H_M - y) / GRID_RES, 0, GRID_H - 1))
        return col, row

    def elevation(self, x: float, y: float) -> float:
        col, row = self._cell(x, y)
        return float(self._h[row, col])

    def elevation_advantage(self, ax: float, ay: float,
                            dx: float, dy: float) -> float:
        """공격자 고도 우위 계수 (0.75 ~ 1.40)."""
        diff = self.elevation(ax, ay) - self.elevation(dx, dy)
        if diff > 120:   return 1.40
        if diff >  60:   return 1.25
        if diff >  20:   return 1.10
        if diff < -120:  return 0.75
        if diff <  -60:  return 0.85
        if diff <  -20:  return 0.93
        return 1.00

    def cover_factor(self, x: float, y: float) -> float:
        """경사·고도 기반 방어 엄폐 계수 (0.0 ~ 0.65)."""
        col, row = self._cell(x, y)
        h = self._h
        r0 = max(0, row - 2); r1 = min(GRID_H - 1, row + 2)
        c0 = max(0, col - 2); c1 = min(GRID_W - 1, col + 2)
        slope = (abs(float(h[r1, col]) - float(h[r0, col])) +
                 abs(float(h[row, c1]) - float(h[row, c0]))) / 2
        elev  = float(h[row, col])
        return float(np.clip(slope * 0.008 + elev * 0.0008, 0.0, 0.65))

    def movement_speed_factor(self, x: float, y: float) -> float:
        """경사 기반 이동 속도 계수 (0.25 ~ 1.0)."""
        col, row = self._cell(x, y)
        h = self._h
        r0 = max(0, row - 1); r1 = min(GRID_H - 1, row + 1)
        c0 = max(0, col - 1); c1 = min(GRID_W - 1, col + 1)
        slope = (abs(float(h[r1, col]) - float(h[r0, col])) +
                 abs(float(h[row, c1]) - float(h[row, c0]))) / 2
        return float(np.clip(1.0 - slope * 0.012, 0.25, 1.0))

    def get_heightmap(self) -> np.ndarray:
        """300×300 고도 배열 반환 (Gradio 지도 렌더링용)."""
        return self._h


# ── 실제 지형 로드 ─────────────────────────────────────────────────────
def _load_terrain():
    try:
        t = KoreaRealTerrain()
        logger.info("[지형] 실제 한국 DEM 로드 완료 (철원 지역)")
        return t
    except Exception as e:
        logger.warning(f"[지형] 실제 DEM 로드 실패: {e} — 합성 지형 사용")
        return _FallbackTerrain()


# ── 폴백: 기존 합성 지형 ──────────────────────────────────────────────

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
