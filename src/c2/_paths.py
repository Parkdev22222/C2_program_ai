"""프로젝트 루트·config·data 경로를 견고하게 해석한다.

파일 깊이(`Path(__file__).parents[N]`)에 의존하지 않고, 위로 올라가며 프로젝트 마커
(`config/`+`data/` 디렉토리, 또는 `pyproject.toml`/`.git`)를 찾아 루트를 결정한다.
따라서 `c2` 패키지가 `src/c2/` 아래에 있든, 다른 위치로 옮겨지든 동일하게 동작한다.

이 모듈은 어떤 계층에도 속하지 않는 순수 경로 유틸(표준 라이브러리만 사용)이며,
domain/application/infrastructure 어디서든 안전하게 import할 수 있다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

_MARKERS = ("pyproject.toml", ".git")


def project_root(start: Optional[Path] = None) -> Path:
    """`start`(기본: 이 파일)에서 위로 올라가며 프로젝트 루트를 찾는다.

    루트 판별: `config/`와 `data/`를 함께 가진 디렉토리, 또는 `pyproject.toml`/`.git`이
    있는 디렉토리. 못 찾으면 표준 `src/c2/...` 레이아웃을 가정한 폴백을 사용한다.
    """
    base = (start or Path(__file__)).resolve()
    for p in (base, *base.parents):
        if (p / "config").is_dir() and (p / "data").is_dir():
            return p
        if any((p / m).exists() for m in _MARKERS):
            return p
    # 폴백: src/c2/_paths.py 기준이면 parents[2]=c2, [3]=src, [4]=<repo>
    parents = base.parents
    return parents[4] if len(parents) > 4 else base.parent


# 프로세스 내 1회 계산 (경로 탐색 비용 절감)
_ROOT: Path = project_root()


def repo_root() -> Path:
    """프로젝트 루트 디렉토리."""
    return _ROOT


def config_path(*parts: str) -> Path:
    """`<repo>/config/<parts...>` 경로."""
    return _ROOT.joinpath("config", *parts)


def data_path(*parts: str) -> Path:
    """`<repo>/data/<parts...>` 경로."""
    return _ROOT.joinpath("data", *parts)
