"""storage/ 下的路径约定。Ingest / Process 的分界线就是 raw()。"""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """优先用 POWERJP_ROOT 环境变量，否则从本文件位置上溯。"""
    env = os.environ.get("POWERJP_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def storage() -> Path:
    return repo_root() / "storage"


def raw(source: str, *parts: str) -> Path:
    """storage/raw/{source}/{parts...} —— 不可变的原始落盘区。"""
    return storage() / "raw" / source / Path(*parts) if parts else storage() / "raw" / source


def structured(source: str, *parts: str) -> Path:
    return (
        storage() / "structured" / source / Path(*parts)
        if parts
        else storage() / "structured" / source
    )


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
