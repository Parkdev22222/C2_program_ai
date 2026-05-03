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

# ── ARMA3 탐색 ──────────────────────────────────────────────────

_STEAM_COMMON_PATHS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Arma 3",
    r"C:\Program Files\Steam\steamapps\common\Arma 3",
    r"D:\Steam\steamapps\common\Arma 3",
    r"D:\SteamLibrary\steamapps\common\Arma 3",
    r"E:\Steam\steamapps\common\Arma 3",
    r"E:\SteamLibrary\steamapps\common\Arma 3",
    r"F:\Steam\steamapps\common\Arma 3",
    r"F:\SteamLibrary\steamapps\common\Arma 3",
]


def find_arma3_exe() -> Optional[str]:
    """
    ARMA3 실행 파일(arma3_x64.exe)을 자동으로 탐색합니다.

    탐색 순서:
    1. Windows 레지스트리 (Steam 앱 설치 경로)
    2. 일반적인 Steam 설치 경로 목록
    3. LOCALAPPDATA의 libraryfolders.vdf 파싱

    Returns:
        실행 파일 절대 경로 또는 None
    """
    if sys.platform != "win32":
        log.warning("ARMA3 자동 탐색은 Windows에서만 지원됩니다.")
        return None

    # 1. 레지스트리
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

    # 2. 일반 경로
    for base in _STEAM_COMMON_PATHS:
        exe = Path(base) / "arma3_x64.exe"
        if exe.exists():
            log.info(f"일반 경로에서 ARMA3 발견: {exe}")
            return str(exe)

    # 3. Steam libraryfolders.vdf 파싱
    try:
        vdf_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Steam" / "steamapps" / "libraryfolders.vdf"
        if not vdf_path.exists():
            vdf_path = Path(r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf")
        if vdf_path.exists():
            content = vdf_path.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                if '"path"' in line.lower():
                    # "path"  "D:\\Games\\Steam"
                    parts = line.strip().split('"')
                    if len(parts) >= 4:
                        lib_path = parts[3].replace("\\\\", "\\")
                        exe = Path(lib_path) / "steamapps" / "common" / "Arma 3" / "arma3_x64.exe"
                        if exe.exists():
                            log.info(f"libraryfolders.vdf에서 ARMA3 발견: {exe}")
                            return str(exe)
    except Exception as e:
        log.debug(f"libraryfolders.vdf 파싱 실패: {e}")

    return None


# ── 미션 폴더 탐색 ──────────────────────────────────────────────

def find_mission_folder(mission_name: str, world: str, multiplayer: bool = True) -> Optional[str]:
    """
    ARMA3 미션 폴더를 자동으로 탐색합니다.

    Args:
        mission_name: 미션 이름 (확장자/맵 제외, 예: "C2AI_BN_VS_BN")
        world: 맵 이름 (예: "Altis", "Stratis")
        multiplayer: True → mpmissions, False → missions

    Returns:
        미션 폴더 절대 경로 또는 None
    """
    folder_name = f"{mission_name}.{world}"
    sub = "mpmissions" if multiplayer else "missions"

    search_roots = []
    # Documents\Arma 3
    for env_var in ("USERPROFILE", "HOMEPATH"):
        base = os.environ.get(env_var, "")
        if base:
            search_roots.append(Path(base) / "Documents" / "Arma 3" / sub)
            search_roots.append(Path(base) / "OneDrive" / "Documents" / "Arma 3" / sub)

    search_roots.append(Path.home() / "Documents" / "Arma 3" / sub)

    for root in search_roots:
        candidate = root / folder_name
        if candidate.exists() and candidate.is_dir():
            log.info(f"미션 폴더 발견: {candidate}")
            return str(candidate)

    log.warning(
        f"미션 폴더를 찾을 수 없음: {folder_name}\n"
        f"  탐색 위치: {[str(r) for r in search_roots]}\n"
        f"  scenarios.yaml의 mission_dir에 절대 경로를 직접 입력하세요."
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
            raise FileNotFoundError(
                "ARMA3 실행 파일(arma3_x64.exe)을 찾을 수 없습니다.\n"
                "scenarios.yaml의 arma3.exe_path에 절대 경로를 입력하세요.\n"
                "예: exe_path: 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Arma 3\\arma3_x64.exe'"
            )
        return exe

    def build_args(self, scenario: dict, mission_dir: str) -> list:
        """ARMA3 실행 인수 목록을 구성합니다."""
        args = []

        # 기본 옵션
        for arg in self._arma3_cfg.get("extra_args", ["-skipIntro", "-noSplash", "-noPause"]):
            args.append(arg)

        # 프로필
        profile = self._arma3_cfg.get("profile", "")
        if profile:
            args.append(f"-name={profile}")

        # 멀티플레이 호스트 모드
        if scenario.get("multiplayer", True):
            args.append("-host")

        # 맵
        world = scenario.get("world", "Altis")
        args.append(f"-world={world}")

        # 미션 경로 (절대 경로 사용)
        args.append(f'-mission="{mission_dir}"')

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

        Args:
            scenario_name:        scenarios.yaml에 등록된 시나리오 키
            override_exe:         ARMA3 exe 경로 강제 지정 (빈 문자열이면 자동 탐색)
            override_mission_dir: 미션 폴더 경로 강제 지정
            override_mission_name: 미션 이름 강제 지정 (custom 시나리오용)
            wait:                 True이면 ARMA3 종료까지 블로킹

        Returns:
            subprocess.Popen 객체
        """
        scenario = self.get_scenario(scenario_name)
        if override_mission_name:
            scenario["mission_name"] = override_mission_name

        exe        = self.resolve_exe(override_exe)
        mission_dir = self.resolve_mission_dir(scenario, override_mission_dir)
        args       = self.build_args(scenario, mission_dir)

        cmd = [exe] + args
        cmd_str = " ".join(cmd)

        log.info(f"시나리오: {scenario.get('display_name', scenario_name)}")
        log.info(f"미션 경로: {mission_dir}")
        log.info(f"실행 명령: {cmd_str}")

        proc = subprocess.Popen(
            cmd_str,
            shell=True,  # 절대 경로 내 공백 처리
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info(f"ARMA3 실행됨 (PID: {proc.pid})")

        if wait:
            proc.wait()

        return proc

    def wait_for_rpt(self, timeout: int = 120) -> Optional[str]:
        """
        ARMA3 실행 후 새로 생성된 .rpt 파일이 나타날 때까지 대기합니다.

        Args:
            timeout: 최대 대기 시간(초)

        Returns:
            .rpt 파일 경로 또는 None (타임아웃)
        """
        log.info(f"ARMA3 .rpt 파일 대기 중 (최대 {timeout}초)...")

        rpt_dirs = [
            os.path.expandvars(r"%LOCALAPPDATA%\Arma 3"),
            os.path.expanduser(r"~\AppData\Local\Arma 3"),
        ]

        # 기존 .rpt 파일 목록 기록
        existing = set()
        for d in rpt_dirs:
            existing.update(glob.glob(os.path.join(d, "*.rpt")))

        deadline = time.time() + timeout
        while time.time() < deadline:
            for d in rpt_dirs:
                current = set(glob.glob(os.path.join(d, "*.rpt")))
                new_files = current - existing
                if new_files:
                    rpt = max(new_files, key=os.path.getmtime)
                    log.info(f".rpt 파일 감지됨: {rpt}")
                    return rpt
            time.sleep(2)

        log.warning(f".rpt 파일을 {timeout}초 안에 찾지 못했습니다.")
        return None
