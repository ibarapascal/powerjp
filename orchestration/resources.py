"""编排层的配置。

pipeline/ 的脚本不感知 Dagster（CLAUDE.md 约定 3），所以这里只需要知道
「在哪个目录、用什么解释器、跑哪个模块」。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dagster import ConfigurableResource


def _default_root() -> str:
    env = os.environ.get("POWERJP_ROOT")
    if env:
        return env
    return str(Path(__file__).resolve().parents[1])


class PowerjpConfig(ConfigurableResource):
    """repo 位置与执行方式。"""

    root: str = _default_root()

    @property
    def root_path(self) -> Path:
        return Path(self.root)

    @property
    def storage(self) -> Path:
        return self.root_path / "storage"

    def raw(self, source: str) -> Path:
        return self.storage / "raw" / source

    def runner(self) -> list[str]:
        """优先用 uv（保证跟 pyproject 的锁定环境一致），退回当前解释器。"""
        uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
        if Path(uv).exists():
            return [uv, "run", "python"]
        import sys

        return [sys.executable]
