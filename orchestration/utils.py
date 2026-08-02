"""asset 与 pipeline 脚本之间的桥。

两个函数覆盖两种 ingest：

- `run_and_report()` —— auto / assisted 类型：真的去跑脚本，把脚本打印的
  `POWERJP_METADATA` 转成 asset metadata。
- `report_manual()` —— manual 类型（要注册/登录后人工投放文件）：**不下载**，
  只扫 raw 目录报告现状。缺文件时返回成功但标注 `status=missing`，
  这样整条链不会因为一个需要人工的源而卡死（CLAUDE.md 架构 §Ingest）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue

from pipeline.core import paths as ppaths
from pipeline.core import report as preport

from .resources import PowerjpConfig


def _to_metadata(raw: dict) -> dict:
    """把脚本回报的 dict 转成 Dagster 能显示的 metadata。

    嵌套结构（逐社/逐年明细）转成 JSON 展示，标量原样保留。
    """
    out: dict = {}
    for key, value in raw.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            out[key] = MetadataValue.json(value)
        elif isinstance(value, bool):
            out[key] = MetadataValue.bool(value)
        elif isinstance(value, int):
            out[key] = MetadataValue.int(value)
        elif isinstance(value, float):
            out[key] = MetadataValue.float(value)
        elif isinstance(value, str) and value.startswith(("http://", "https://")):
            out[key] = MetadataValue.url(value)
        else:
            out[key] = MetadataValue.text(str(value))
    return out


def run_and_report(
    context: AssetExecutionContext,
    cfg: PowerjpConfig,
    module: str,
    *args: str,
    timeout: int = 60 * 60 * 4,
) -> MaterializeResult:
    """跑 `python -m {module} {args}`，把 stdout 里的 metadata 收上来。"""
    cmd = [*cfg.runner(), "-m", module, *args]
    context.log.info("执行：%s（cwd=%s）", " ".join(cmd), cfg.root)

    proc = subprocess.run(
        cmd, cwd=cfg.root, capture_output=True, text=True, timeout=timeout, check=False
    )
    for line in proc.stdout.splitlines()[-60:]:
        context.log.info(line)
    if proc.returncode != 0:
        for line in proc.stderr.splitlines()[-60:]:
            context.log.error(line)
        raise RuntimeError(f"{module} 退出码 {proc.returncode}")

    meta = preport.parse(proc.stdout)
    if not meta:
        context.log.warning("%s 没有回报 metadata（缺 report.emit？）", module)
    return MaterializeResult(metadata=_to_metadata(meta))


def report_manual(
    context: AssetExecutionContext,
    cfg: PowerjpConfig,
    source: str,
    *,
    hint: str,
    patterns: tuple[str, ...] = ("*",),
) -> MaterializeResult:
    """只扫 raw 目录报告现状，不下载。用于需要人工投放的源。"""
    directory = cfg.raw(source)
    files: list[Path] = []
    if directory.exists():
        for pattern in patterns:
            files.extend(p for p in directory.rglob(pattern) if p.is_file())

    size = ppaths.dir_size(directory)
    status = "ok" if files else "missing"
    if status == "missing":
        context.log.warning("%s 尚未投放任何文件。%s", source, hint)
    else:
        context.log.info("%s：%d 个文件、%s", source, len(files),
                         preport.human_bytes(size))

    return MaterializeResult(
        metadata=_to_metadata({
            "status": status,
            "path": str(directory),
            "files": len(files),
            "bytes": size,
            "size_human": preport.human_bytes(size),
            "ingest_type": "manual / assisted —— 需人工介入",
            "how_to": hint,
            "newest": max((p.name for p in files), default=None),
        })
    )
