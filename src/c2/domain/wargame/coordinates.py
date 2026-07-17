"""좌표 변환 유틸리티 — 엔진 내부 미터(m) ↔ 실세계 위경도(WGS84)

작전 지역 기준점: 철원 지역 (DMZ 인근)
  lat=38.0, lon=127.0 → 엔진 내부 (0, 0)
  30km × 30km 작전 범위: lat 38.0~38.27, lon 127.0~127.34
"""
import math

REF_LAT = 38.0   # 철원 지역 (DMZ 인근)
REF_LON = 127.0
METERS_PER_DEG_LAT = 111000.0
METERS_PER_DEG_LON = 111000.0 * math.cos(math.radians(REF_LAT))


def xy_to_latlon(x_m: float, y_m: float) -> tuple:
    """(x_m, y_m) 미터 → (lat, lon) 위경도 변환. 소수점 6자리."""
    lat = round(REF_LAT + y_m / METERS_PER_DEG_LAT, 6)
    lon = round(REF_LON + x_m / METERS_PER_DEG_LON, 6)
    return lat, lon


def latlon_to_xy(lat: float, lon: float) -> tuple:
    """(lat, lon) 위경도 → (x_m, y_m) 미터 변환. 정수 반올림."""
    y_m = round((lat - REF_LAT) * METERS_PER_DEG_LAT)
    x_m = round((lon - REF_LON) * METERS_PER_DEG_LON)
    return int(x_m), int(y_m)


def waypoints_xy_to_latlon(waypoints: list) -> list:
    """[[x,y],...] 미터 리스트 → [[lat,lon],...] 위경도 리스트"""
    result = []
    for wp in waypoints:
        if isinstance(wp, (list, tuple)) and len(wp) == 2:
            lat, lon = xy_to_latlon(wp[0], wp[1])
            result.append([lat, lon])
        elif isinstance(wp, dict):
            lat, lon = xy_to_latlon(wp.get("x", 0), wp.get("y", 0))
            result.append([lat, lon])
        else:
            result.append(wp)
    return result


def waypoints_latlon_to_xy(waypoints: list) -> list:
    """[[lat,lon],...] 위경도 리스트 → [[x,y],...] 미터 리스트"""
    result = []
    for wp in waypoints:
        if isinstance(wp, (list, tuple)) and len(wp) == 2:
            x, y = latlon_to_xy(wp[0], wp[1])
            result.append([x, y])
        elif isinstance(wp, dict):
            x, y = latlon_to_xy(
                wp.get("lat", wp.get("latitude", 0)),
                wp.get("lon", wp.get("longitude", 0)),
            )
            result.append([x, y])
        else:
            result.append(wp)
    return result


def is_latlon_coords(waypoints: list) -> bool:
    """waypoints가 위경도 형식인지 판별 (lat 범위 -90~90, 첫 값이 소수점)"""
    if not waypoints:
        return False
    wp = waypoints[0]
    if isinstance(wp, dict):
        # lat/lon 키가 있으면 위경도
        if "lat" in wp or "latitude" in wp or "lon" in wp or "longitude" in wp:
            return True
        return False
    if isinstance(wp, (list, tuple)) and len(wp) == 2:
        v = float(wp[0])
        return -90.0 <= v <= 90.0 and v != round(v)  # 정수가 아닌 소수이면 lat/lon
    return False
