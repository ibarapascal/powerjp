"""JEPX スポット市場取引結果 ingest（type: auto）。

站点用 XHR 取数，两个 GET 接口（**必须带 Referer，否则返回空 200**）：

    /js/get_graph_year.php?dir=spot_summary          → "latestYear,oldYear"
    /js/csv_read.php?dir=spot_summary&file=spot_summary_{year}.csv

`{year}` 是**年度**（fiscal year）：2024 → 2024-04-01 ~ 2025-03-31，
一年度 17,520 行 = 365 × 48 断面。

raw 层保持原始字节不转码（Shift_JIS 原样落盘），编码转换是 Process 阶段的事。

用法：
    python -m pipeline.sources.jepx.ingest              # 最新年度
    python -m pipeline.sources.jepx.ingest --all
    python -m pipeline.sources.jepx.ingest --year 2024
    python -m pipeline.sources.jepx.ingest --years 3    # 最近 3 年度
    python -m pipeline.sources.jepx.ingest --dry-run
"""

from __future__ import annotations

import argparse
import logging

from pipeline.core import fetch, paths, report

BASE = "https://www.jepx.jp"
REFERER = f"{BASE}/electricpower/market-data/spot/"
DIR = "spot_summary"

# 一年度应有的行数（含表头）：365 或 366 天 × 48 断面 + 1
ROWS_PER_FY = {365: 17_521, 366: 17_569}

log = logging.getLogger("powerjp.jepx")


def year_range() -> tuple[int, int]:
    """返回 (oldest, latest) 年度。"""
    resp = fetch.get(
        f"{BASE}/js/get_graph_year.php?dir={DIR}", headers={"Referer": REFERER}
    )
    latest_s, old_s = resp.text.strip().split(",")[:2]
    return int(old_s), int(latest_s)


def fetch_year(year: int, *, force: bool) -> tuple[int, int]:
    """下载一年度 CSV，返回 (字节数, 行数)。"""
    name = f"{DIR}_{year}.csv"
    dest = paths.raw("jepx", str(year)) / name
    url = f"{BASE}/js/csv_read.php?dir={DIR}&file={name}"

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force and dest.stat().st_size > 0:
        log.info("已存在，跳过：%s", name)
    else:
        resp = fetch.get(url, headers={"Referer": REFERER})
        if not resp.content:
            # 空 200 是这个站点的典型失败模式（缺 Referer 或该年度无数据）
            log.warning("%s 返回空内容，跳过", name)
            return 0, 0
        dest.write_bytes(resp.content)
        log.info("下载完成：%s (%s)", name, report.human_bytes(len(resp.content)))

    data = dest.read_bytes()
    lines = data.count(b"\n")
    expected = set(ROWS_PER_FY.values())
    if lines and lines not in expected and lines + 1 not in expected:
        log.warning("%s 行数 %d 不是完整年度（期待 %s）—— 可能是进行中的年度",
                    name, lines, sorted(expected))
    return len(data), lines


def main() -> None:
    ap = argparse.ArgumentParser(description="JEPX 现货价格 ingest")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="全部年度")
    g.add_argument("--year", type=int, help="指定年度（fiscal year）")
    g.add_argument("--years", type=int, metavar="N", help="最近 N 个年度")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    fetch.setup_logging()
    oldest, latest = year_range()
    log.info("上游可得年度范围：%d–%d", oldest, latest)

    if args.all:
        targets = list(range(oldest, latest + 1))
    elif args.year:
        targets = [args.year]
    elif args.years:
        targets = list(range(max(oldest, latest - args.years + 1), latest + 1))
    else:
        targets = [latest]

    if args.dry_run:
        log.info("dry-run — 将取 %d 个年度：%s\n落盘：%s",
                 len(targets), targets, paths.raw("jepx"))
        return

    per_year: dict[int, dict[str, int]] = {}
    for year in targets:
        size, lines = fetch_year(year, force=args.force)
        if size:
            per_year[year] = {"bytes": size, "rows": lines}

    total = paths.dir_size(paths.raw("jepx"))
    got = sorted(per_year)
    log.info("完成 %d/%d 个年度，合计 %s", len(per_year), len(targets),
             report.human_bytes(total))

    report.emit(
        path=str(paths.raw("jepx")),
        bytes=total,
        size_human=report.human_bytes(total),
        years_requested=targets,
        years_fetched=got,
        time_range=f"FY{got[0]}–FY{got[-1]}" if got else None,
        rows=sum(v["rows"] for v in per_year.values()),
        per_year=per_year,
        upstream_range=f"{oldest}-{latest}",
        encoding="Shift_JIS（原样落盘，不转码）",
        licence="TO VERIFY — JEPX 利用規約",
        source_url=REFERER,
    )


if __name__ == "__main__":
    main()
