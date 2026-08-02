"""ingest / process 脚本向编排层回报 metadata 的统一通道。

脚本本身不感知 Dagster（见 CLAUDE.md 约定 3）。约定是：脚本在 stdout 最后
打印一行 `POWERJP_METADATA {json}`，编排层的 run_and_report() 解析它并写进
asset metadata。脚本单独跑时这一行只是普通输出，不影响使用。
"""

from __future__ import annotations

import json
from typing import Any

MARKER = "POWERJP_METADATA"


def emit(**fields: Any) -> None:
    """打印一行机器可读的 metadata。约定字段见 CLAUDE.md 约定 4。

    常用字段：rows, bytes, path, time_range, demo_url, notes
    """
    print(f"{MARKER} {json.dumps(fields, ensure_ascii=False, default=str)}")


def parse(stdout: str) -> dict[str, Any]:
    """从脚本 stdout 中取出最后一条 metadata。没有则返回空 dict。"""
    result: dict[str, Any] = {}
    for line in stdout.splitlines():
        if line.startswith(MARKER):
            try:
                result = json.loads(line[len(MARKER) :].strip())
            except json.JSONDecodeError:
                continue
    return result


def human_bytes(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step:
            return f"{value:.1f}{unit}"
        value /= step
    return f"{value:.1f}PB"
