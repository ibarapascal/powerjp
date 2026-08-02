"""GEM Global Integrated Power Tracker ingest（type: **manual**）。

## 为什么是 manual

下载在一个 **gated 表单**后面，必须提交 name / email / organization / sector /
country / use-case（≥100 字符）/ license 同意。表单是 Shadow DOM 里的 web component，
后端 Supabase：

    POST .../rest/v1/rpc/mint_submission   → capability_token
    POST .../functions/v1/presign  Bearer  → 预签名 S3 URL（含凭据参数，有时效）

**凭据与个人信息不进 repo**，所以这一步永远是人工。本脚本只负责
「扫 raw 目录报告现状 + 解析已投放的 xlsx」。

## 人工取数步骤

1. 打开 https://globalenergymonitor.org/projects/global-integrated-power-tracker/
2. 「What's inside?」区块 → 点 **`Download data`** 页签（不是 Summary tables）
3. 填表：name / email / organization / sector / country / use（≥100 字符）
4. 勾 license（原文：*I have reviewed and understand Global Energy Monitor's
   Creative Commons Public License*），email_optin 按需
5. Submit → 出现下载链接 → 下载 xlsx
6. 放到 `storage/raw/gem/{YYYY-MM-DD}/`，并把版本/日期记进 INGEST.md 的「取得记录」

## 内容（2026-03 release 实测）

全球 182,428 行 / **日本 12,074 行**，52 列，含 **Latitude / Longitude /
Location accuracy**，以及 `Plant / Project name (local)`（**日文原名**，
所以跟 OCCTO 匹配不需要罗马字转换）。

用法：
    python -m pipeline.sources.gem.ingest            # 扫描 + 汇总
    python -m pipeline.sources.gem.ingest --country Japan
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from pipeline.core import fetch, paths, report

SHEET = "Power facilities"
log = logging.getLogger("powerjp.gem")


def main() -> None:
    ap = argparse.ArgumentParser(description="GEM GIPT ingest（人工投放后扫描）")
    ap.add_argument("--country", default="Japan")
    args = ap.parse_args()
    fetch.setup_logging()

    root = paths.raw("gem")
    files = sorted(root.rglob("*.xlsx")) if root.exists() else []
    if not files:
        log.warning("%s 下没有 xlsx。这是 manual 源，需人工下载后投放 —— "
                    "步骤见 pipeline/sources/gem/INGEST.md", root)
        report.emit(path=str(root), status="missing", files=0,
                    ingest_type="manual —— gated 表单，需人工提交后下载",
                    source_url="https://globalenergymonitor.org/projects/"
                               "global-integrated-power-tracker/")
        return

    latest = files[-1]
    log.info("使用 %s", latest.relative_to(root))

    try:
        import openpyxl
    except ImportError:
        log.error("缺 openpyxl，先 `uv add openpyxl`")
        return

    wb = openpyxl.load_workbook(latest, read_only=True)
    if SHEET not in wb.sheetnames:
        log.error("没有『%s』sheet，实际有：%s", SHEET, wb.sheetnames)
        return
    rows = wb[SHEET].iter_rows(values_only=True)
    hdr = next(rows)
    col = {h: i for i, h in enumerate(hdr) if h}

    need = ["Country/area", "Type", "Latitude", "Longitude"]
    missing = [c for c in need if c not in col]
    if missing:
        log.error("列结构变了，缺：%s", missing)
        return

    total = target = with_coord = 0
    by_type: Counter = Counter()
    for r in rows:
        total += 1
        if str(r[col["Country/area"]]).strip() != args.country:
            continue
        target += 1
        by_type[r[col["Type"]]] += 1
        if r[col["Latitude"]] is not None and r[col["Longitude"]] is not None:
            with_coord += 1

    size = paths.dir_size(root)
    log.info("全球 %d 行 / %s %d 行 / 有坐标 %d（%.1f%%）",
             total, args.country, target, with_coord,
             with_coord / target * 100 if target else 0)
    log.info("按类型：%s", dict(by_type))

    report.emit(
        path=str(root),
        bytes=size,
        size_human=report.human_bytes(size),
        status="ok",
        files=len(files),
        latest_file=latest.name,
        rows=target,
        rows_global=total,
        with_coordinates=with_coord,
        by_type=dict(by_type),
        has_coordinates=True,
        columns=len(hdr),
        licence="Creative Commons Public License（下载时的 license 复选框原文确认）",
        source_url="https://globalenergymonitor.org/projects/global-integrated-power-tracker/",
    )


if __name__ == "__main__":
    main()
