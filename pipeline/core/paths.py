"""storage/ 下的路径约定。Ingest / Process 的分界线就是 raw()。"""

from __future__ import annotations

import os
import re
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


def _ver_key(name: str):
    """`v1.6.0` / `P03-13` / `2026-08-01` を比較可能なタプルにする。

    ⚠️ 文字列のソートでは `v1.10.0 < v1.6.0` になってしまう。
    数字の並びを取り出して数値として比べる。
    """
    nums = [int(x) for x in re.findall(r"\d+", name)]
    return (nums, name)


def latest_version(source: str, pattern: str = "*") -> Path:
    """`storage/raw/{source}/` の**最新**のサブディレクトリ。

    ## ⚠️ バージョンやダウンロード日をコードに書かない

    `AJG_TAG = "v1.6.0"`、`raw("gem") / "2026-08-01"` のように固定すると、
    新しいデータを取り込んでも**古い方を読み続ける**。しかもエラーは出ず、
    黙って過去のデータで結果を出す。

    上流が更新されたら自動で追随し、追随できないときは**落ちる**ようにする。
    """
    base = raw(source)
    dirs = [p for p in base.glob(pattern) if p.is_dir()]
    if not dirs:
        raise FileNotFoundError(
            f"{base} に取り込み済みのデータがない。先に ingest を実行する")
    return max(dirs, key=lambda p: _ver_key(p.name))


def latest_file(source: str, glob_pat: str, subdir: str | None = None) -> Path:
    """`storage/raw/{source}/[{subdir}/]` 直下で `glob_pat` に一致する最新ファイル。

    GEM のようにファイル名へ版が入る源（`...-March-2026-II.xlsx`）用。
    ⚠️ 更新日ではなく**名前**で比べる。rsync でコピーすると mtime は当てにならない。
    """
    base = latest_version(source) if subdir is None else raw(source, subdir)
    hits = sorted(base.glob(glob_pat), key=lambda p: _ver_key(p.name))
    if not hits:
        raise FileNotFoundError(f"{base}/{glob_pat} に該当なし。先に ingest を実行する")
    return hits[-1]


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
