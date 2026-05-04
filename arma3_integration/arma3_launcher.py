"""
ARMA3 자동 런처

scenarios.yaml에 등록된 시나리오명만 지정하면
ARMA3 실행 파일 탐색 → 미션 폴더 탐색 → ARMA3 프로세스 실행을 자동으로 처리합니다.
"""

import glob
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

SCENARIOS_YAML = Path(__file__).parent / "scenarios.yaml"

# ── 플랫폼 감지 헬퍼 ────────────────────────────────────────────

def _crossover_bottles() -> list:
    """CrossOver 병(Bottle) 디렉토리 목록을 반환합니다."""
    base = Path.home() / "Library" / "Application Support" / "CrossOver" / "Bottles"
    if not base.exists():
        return []
    return [p for p in base.iterdir() if p.is_dir()]


def _find_crossover_wine() -> Optional[str]:
    """CrossOver wine 실행 파일 경로를 반환합니다."""
    candidates = [
        "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine",
        str(Path.home() / "Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine"),
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


# ── ARMA3 실행 파일 탐색 ─────────────────────────────────────────

def find_arma3_exe() -> Optional[str]:
    """
    ARMA3 실행 파일을 플랫폼에 맞게 자동으로 탐색합니다.

    macOS: CrossOver 병 → Steam for Mac .app
    Windows: 레지스트리 → 일반 Steam 경로 → libraryfolders.vdf

    Returns:
        실행 파일 절대 경로 또는 None
    """
    if sys.platform == "darwin":
        return _find_arma3_exe_macos()
    if sys.platform == "win32":
        return _find_arma3_exe_windows()
    return None


def _find_arma3_exe_macos() -> Optional[str]:
    """macOS: CrossOver 병 또는 Steam.app 에서 ARMA3 실행 파일 탐색."""
    arma3_rel_paths = [
        "drive_c/Program Files (x86)/Steam/steamapps/common/Arma 3/arma3_x64.exe",
        "drive_c/Program Files/Steam/steamapps/common/Arma 3/arma3_x64.exe",
    ]
    for bottle in _crossover_bottles():
        for rel in arma3_rel_paths:
            exe = bottle / rel
            if exe.exists():
                log.info(f"CrossOver 병에서 ARMA3 발견: {exe}")
                return str(exe)

    # macOS 네이티브 Steam (구버전 or 별도 설치)
    native_paths = [
        Path.home() / "Library/Application Support/Steam/steamapps/common/Arma 3/ArmA3.app",
        Path("/Applications/ArmA 3.app"),
        Path.home() / "Applications/ArmA 3.app",
    ]
    for p in native_paths:
        if p.exists():
            log.info(f"macOS 네이티브 ARMA3 발견: {p}")
            return str(p)

    return None


def _find_arma3_exe_windows() -> Optional[str]:
    """Windows: 레지스트리 → 일반 경로 → libraryfolders.vdf."""
    steam_paths = [
        r"C:\Program Files (x86)\Steam\steamapps\common\Arma 3",
        r"C:\Program Files\Steam\steamapps\common\Arma 3",
        r"D:\Steam\steamapps\common\Arma 3",
        r"D:\SteamLibrary\steamapps\common\Arma 3",
        r"E:\Steam\steamapps\common\Arma 3",
        r"E:\SteamLibrary\steamapps\common\Arma 3",
        r"F:\Steam\steamapps\common\Arma 3",
        r"F:\SteamLibrary\steamapps\common\Arma 3",
    ]

    # 레지스트리
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 107410",
        )
        install_dir, _ = winreg.QueryValueEx(key, "InstallLocation")
        winreg.CloseKey(key)
        exe = Path(install_dir) / "arma3_x64.exe"
        if exe.exists():
            log.info(f"레지스트리에서 ARMA3 발견: {exe}")
            return str(exe)
    except Exception:
        pass

    # 일반 경로
    for base in steam_paths:
        exe = Path(base) / "arma3_x64.exe"
        if exe.exists():
            log.info(f"일반 경로에서 ARMA3 발견: {exe}")
            return str(exe)

    # libraryfolders.vdf
    try:
        vdf = Path(os.environ.get("LOCALAPPDATA", "")) / "Steam/steamapps/libraryfolders.vdf"
        if not vdf.exists():
            vdf = Path(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf")
        if vdf.exists():
            for line in vdf.read_text(encoding="utf-8", errors="ignore").splitlines():
                if '"path"' in line.lower():
                    parts = line.strip().split('"')
                    if len(parts) >= 4:
                        lib = parts[3].replace("\\\\", "\\")
                        exe = Path(lib) / "steamapps/common/Arma 3/arma3_x64.exe"
                        if exe.exists():
                            log.info(f"libraryfolders.vdf에서 ARMA3 발견: {exe}")
                            return str(exe)
    except Exception as e:
        log.debug(f"libraryfolders.vdf 파싱 실패: {e}")

    return None


# ── 미션 폴더 탐색 ──────────────────────────────────────────────

def find_mission_folder(mission_name: str, world: str, multiplayer: bool = True) -> Optional[str]:
    """
    ARMA3 미션 폴더를 플랫폼에 맞게 자동으로 탐색합니다.

    Args:
        mission_name: 미션 이름 (확장자/맵 제외, 예: "C2AI_BN_VS_BN")
        world: 맵 이름 (예: "Altis", "Stratis")
        multiplayer: True → mpmissions, False → missions
    """
    folder_name = f"{mission_name}.{world}"
    sub = "mpmissions" if multiplayer else "missions"
    home = Path.home()

    search_roots: list[Path] = []

    if sys.platform == "darwin":
        # ① CrossOver — 모든 병의 Windows Documents 탐색
        for bottle in _crossover_bottles():
            for user_dir in (bottle / "drive_c" / "users").iterdir() if (bottle / "drive_c" / "users").exists() else []:
                search_roots.append(user_dir / "Documents" / "Arma 3" / sub)
            for user_dir in (bottle / "drive_c" / "Users").iterdir() if (bottle / "drive_c" / "Users").exists() else []:
                search_roots.append(user_dir / "Documents" / "Arma 3" / sub)

        # ② macOS 네이티브 Steam (com.vpltd.Arma3 번들 ID 경로) — 최우선
        _vpltd = home / "Library" / "Application Support" / "com.vpltd.Arma3" / "GameDocuments" / "Arma 3"
        search_roots += [
            _vpltd / sub,
            _vpltd / ("missions" if sub == "mpmissions" else "mpmissions"),  # 역방향도 시도
        ]

        # ③ macOS 네이티브 Steam (구버전)
        search_roots += [
            home / "Documents" / "Arma 3" / sub,
            home / "Library" / "Application Support" / "Arma 3" / sub,
        ]

    elif sys.platform == "win32":
        for env_var in ("USERPROFILE", "HOMEPATH"):
            base = os.environ.get(env_var, "")
            if base:
                search_roots.append(Path(base) / "Documents" / "Arma 3" / sub)
                search_roots.append(Path(base) / "OneDrive" / "Documents" / "Arma 3" / sub)
        search_roots.append(home / "Documents" / "Arma 3" / sub)

    else:
        search_roots.append(home / "Documents" / "Arma 3" / sub)

    for root in search_roots:
        candidate = root / folder_name
        if candidate.exists() and candidate.is_dir():
            log.info(f"미션 폴더 발견: {candidate}")
            return str(candidate)

    # 존재하는 첫 번째 부모 폴더 → 복사 위치 안내
    copy_target = next((str(r) for r in search_roots if r.parent.exists()), str(search_roots[0]))
    log.warning(
        f"미션 폴더를 찾을 수 없음: {folder_name}\n"
        f"  → 아래 경로에 미션 폴더를 복사하세요:\n"
        f"     {copy_target}/\n"
        f"  복사 명령 예시:\n"
        f"     cp -r arma3_integration/mission_template/{folder_name} \"{copy_target}/\"\n"
        f"  또는 scenarios.yaml의 mission_dir에 절대 경로를 직접 입력하세요."
    )
    return None


# ── 메인 런처 ────────────────────────────────────────────────────

class Arma3Launcher:
    """
    시나리오명 하나로 ARMA3를 자동 실행하는 런처.

    사용 예:
        launcher = Arma3Launcher()
        proc = launcher.launch("bn_vs_bn")
        # proc: subprocess.Popen 객체
    """

    def __init__(self, scenarios_path: str = str(SCENARIOS_YAML)):
        with open(scenarios_path, encoding="utf-8") as f:
            self._cfg = yaml.safe_load(f)
        self._arma3_cfg = self._cfg.get("arma3", {})
        self._scenarios = self._cfg.get("scenarios", {})

    # ── 공개 API ─────────────────────────────────────────────────

    def list_scenarios(self) -> dict:
        """등록된 시나리오 목록을 반환합니다."""
        return {
            name: {
                "display_name": s.get("display_name", name),
                "description": s.get("description", ""),
                "world": s.get("world", ""),
            }
            for name, s in self._scenarios.items()
        }

    def get_scenario(self, name: str) -> dict:
        """시나리오 설정을 반환합니다. 없으면 KeyError."""
        if name not in self._scenarios:
            available = list(self._scenarios.keys())
            raise KeyError(
                f"시나리오 '{name}'을 찾을 수 없습니다.\n"
                f"사용 가능한 시나리오: {available}\n"
                f"새 시나리오는 arma3_integration/scenarios.yaml에 추가하세요."
            )
        return dict(self._scenarios[name])

    def resolve_mission_dir(self, scenario: dict, override_dir: str = "") -> str:
        """
        미션 폴더 경로를 결정합니다.

        우선순위: 함수 인수 override_dir > scenarios.yaml mission_dir > 자동 탐색
        """
        if override_dir:
            return override_dir
        if scenario.get("mission_dir"):
            return scenario["mission_dir"]

        mission_name = scenario.get("mission_name", "")
        world = scenario.get("world", "Altis")
        mp = scenario.get("multiplayer", True)

        if not mission_name:
            raise ValueError(
                "mission_name이 비어 있습니다. "
                "scenarios.yaml 또는 --mission-name 옵션으로 지정하세요."
            )

        folder = find_mission_folder(mission_name, world, mp)
        if folder is None:
            raise FileNotFoundError(
                f"미션 폴더를 찾을 수 없습니다: {mission_name}.{world}\n"
                f"Documents\\Arma 3\\{'mpmissions' if mp else 'missions'}\\ 에 미션이 있는지 확인하거나\n"
                f"scenarios.yaml의 mission_dir에 절대 경로를 입력하세요."
            )
        return folder

    def resolve_exe(self, override_exe: str = "") -> str:
        """ARMA3 실행 파일 경로를 결정합니다."""
        if override_exe:
            return override_exe
        cfg_exe = self._arma3_cfg.get("exe_path", "")
        if cfg_exe:
            return cfg_exe

        exe = find_arma3_exe()
        if exe is None:
            if sys.platform == "darwin":
                hint = (
                    "macOS에서 ARMA3 실행 파일을 찾을 수 없습니다.\n"
                    "scenarios.yaml의 arma3.exe_path에 경로를 입력하세요.\n\n"
                    "CrossOver 사용 시 예:\n"
                    "  exe_path: '~/Library/Application Support/CrossOver/Bottles/"
                    "Steam/drive_c/Program Files (x86)/Steam/steamapps/common/Arma 3/arma3_x64.exe'\n\n"
                    "CrossOver 병 목록 확인:\n"
                    "  ls ~/Library/Application\\ Support/CrossOver/Bottles/"
                )
            else:
                hint = (
                    "scenarios.yaml의 arma3.exe_path에 절대 경로를 입력하세요.\n"
                    "예: exe_path: 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Arma 3\\arma3_x64.exe'"
                )
            raise FileNotFoundError(hint)
        return exe

    def build_args(self, scenario: dict, mission_dir: str) -> list:
        """ARMA3 실행 인수 목록을 구성합니다."""
        args = []
        for arg in self._arma3_cfg.get("extra_args", ["-skipIntro", "-noSplash", "-noPause"]):
            args.append(arg)
        profile = self._arma3_cfg.get("profile", "")
        if profile:
            args.append(f"-name={profile}")

        # ARMA3 -mission= 은 폴더 이름(C2AI_BN_VS_BN.Altis)만 받음 — 절대 경로 불가
        mission_folder_name = Path(mission_dir).name  # e.g. "C2AI_BN_VS_BN.Altis"

        if scenario.get("multiplayer", True):
            # 멀티플레이: 로컬 호스트로 바로 미션 시작
            args.append("-host")
            args.append(f"-world={scenario.get('world', 'Altis')}")
            args.append(f"-mission={mission_folder_name}")
        else:
            # 싱글플레이: init 파라미터로 미션 직접 로드
            args.append(f"-world={scenario.get('world', 'Altis')}")
            args.append(
                f'-init=playMission ["{mission_folder_name}", "{scenario.get("world", "Altis")}"];'
            )

        return args

    def launch(
        self,
        scenario_name: str,
        override_exe: str = "",
        override_mission_dir: str = "",
        override_mission_name: str = "",
        wait: bool = False,
    ) -> subprocess.Popen:
        """
        지정된 시나리오로 ARMA3를 실행합니다.

        macOS:   CrossOver wine으로 arma3_x64.exe 실행
                 .app이면 open -a "ArmA 3" --args ... 실행
        Windows: arma3_x64.exe 직접 실행

        Returns:
            subprocess.Popen 객체
        """
        scenario = self.get_scenario(scenario_name)
        if override_mission_name:
            scenario["mission_name"] = override_mission_name

        exe         = self.resolve_exe(override_exe)
        mission_dir = self.resolve_mission_dir(scenario, override_mission_dir)
        args        = self.build_args(scenario, mission_dir)

        log.info(f"시나리오: {scenario.get('display_name', scenario_name)}")
        log.info(f"미션 경로: {mission_dir}")

        if sys.platform == "darwin":
            proc = self._launch_macos(exe, args)
        else:
            proc = self._launch_windows(exe, args)

        log.info(f"ARMA3 실행됨 (PID: {proc.pid})")
        if wait:
            proc.wait()
        return proc

    def _launch_macos(self, exe: str, args: list) -> subprocess.Popen:
        """macOS 전용 실행 — CrossOver wine 또는 .app open."""
        if exe.endswith(".exe"):
            # CrossOver wine 경유 실행
            wine = _find_crossover_wine()
            if wine is None:
                raise FileNotFoundError(
                    "CrossOver wine 실행 파일을 찾을 수 없습니다.\n"
                    "/Applications/CrossOver.app 이 설치되어 있는지 확인하세요."
                )
            # WINEPREFIX: arma3_x64.exe 경로에서 병 경로 역산
            # 예) .../Bottles/Steam/drive_c/...arma3_x64.exe → .../Bottles/Steam
            exe_path = Path(exe)
            bottle_path = None
            for part in exe_path.parts:
                if part == "drive_c":
                    idx = exe_path.parts.index(part)
                    bottle_path = Path(*exe_path.parts[:idx])
                    break

            env = os.environ.copy()
            if bottle_path:
                env["WINEPREFIX"] = str(bottle_path)
                log.info(f"WINEPREFIX: {bottle_path}")

            cmd = [wine, exe] + args
            log.info(f"실행 명령 (wine): {' '.join(cmd)}")
            return subprocess.Popen(cmd, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif exe.endswith(".app") or Path(exe).suffix == "":
            # macOS 네이티브 .app
            app_name = Path(exe).stem  # "ArmA 3" 등
            cmd = ["open", "-a", app_name, "--args"] + args
            log.info(f"실행 명령 (open): {' '.join(cmd)}")
            return subprocess.Popen(cmd,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        else:
            # 직접 실행 (실행 권한 필요)
            cmd = [exe] + args
            log.info(f"실행 명령: {' '.join(cmd)}")
            return subprocess.Popen(cmd,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _launch_windows(self, exe: str, args: list) -> subprocess.Popen:
        """Windows 전용 실행."""
        cmd_str = f'"{exe}" ' + " ".join(args)
        log.info(f"실행 명령: {cmd_str}")
        return subprocess.Popen(cmd_str, shell=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_for_rpt(self, timeout: int = 120) -> Optional[str]:
        """
        ARMA3 실행 후 새로 생성된 로그 파일(.rpt/.dat)이 나타날 때까지 대기합니다.

        Returns:
            로그 파일 경로 또는 None (타임아웃)
        """
        from relay import _log_search_patterns
        log.info(f"ARMA3 로그 파일 대기 중 (최대 {timeout}초)...")

        patterns = _log_search_patterns()

        def _expand(pat):
            return set(glob.glob(pat, recursive=True))

        # 기존 파일 목록 스냅샷
        existing = set()
        for pat in patterns:
            existing.update(_expand(pat))

        deadline = time.time() + timeout
        while time.time() < deadline:
            for pat in patterns:
                current = _expand(pat)
                new_files = current - existing
                if new_files:
                    rpt = max(new_files, key=os.path.getmtime)
                    log.info(f"로그 파일 감지됨: {rpt}")
                    return rpt
            time.sleep(2)

        log.warning(f"로그 파일을 {timeout}초 안에 찾지 못했습니다.")
        return None
