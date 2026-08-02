"""FIT/FIP 認定・導入量（都道府県別 / 市町村別）ingest（type: auto）。

経産省「再生可能エネルギー 事業計画認定情報」公表サイト（fit-portal.go.jp）。
**无需凭据。**

## 拿到的是什么

**市町村级的再能源认定量与导入量，按电源种和容量档细分** ——
这是分布式光伏/风电的**空间分布**，区域级需给实绩完全看不到。

三张表，季度更新，回溯到 2019 年：

| 表 | 内容 | 文件数 |
|---|---|---|
| A表 | 都道府県別 認定・導入量 | 约 35 |
| **B表** | **市町村別 認定・導入量** ← 主目标 | 约 35 |
| C表 | 買取電力量及び買取金額の推移 | 1 |

B表的列结构（实测 2025-12 版）：

    太陽光発電設備  10kW未満（うち自家発電設備併設）
                    10kW以上（うち 50kW未満 / 50–500kW / 500–1,000kW /
                              1,000–2,000kW / 2,000kW以上）
    風力発電設備    20kW未満 / 20kW以上（**うち洋上風力**）
    …中小水力・地熱・バイオマス 同様

## ⚠️ 没有坐标

本源是**市町村级聚合**，不是逐设施清单，**没有经纬度**。
它解决「哪个市町村装了多少」，不解决「具体那台在哪」。

## 获取

站点是 Salesforce 架构，下载走 `servlet.FileDownload?file=<15位ID>`。
**ID 会变**（每次公表换新 ID），所以 ingest 每次都重新解析公表页取 ID，不写死。

用法：
    python -m pipeline.sources.fit.ingest              # 全部 A/B/C 表
    python -m pipeline.sources.fit.ingest --table B    # 只要市町村别
    python -m pipeline.sources.fit.ingest --latest 4   # 只要最近 4 期
    python -m pipeline.sources.fit.ingest --dry-run
"""

from __future__ import annotations

import argparse
import html
import logging
import re

from pipeline.core import fetch, paths, report

PAGE = "https://www.fit-portal.go.jp/PublicInfoSummary"
DL = "https://www.fit-portal.go.jp/servlet/servlet.FileDownload?file="

log = logging.getLogger("powerjp.fit")

# 表名形如「A表 都道府県別認定・導入量（２０２５年１２月末時点）」
TABLE_RE = re.compile(r"^([ABC])表")
# 全角数字 → 半角，用来从表名里抽年月
ZEN = str.maketrans("０１２３４５６７８９", "0123456789")


def parse_period(label: str) -> str | None:
    m = re.search(r"[（(]\s*([０-９\d]{4})\s*年\s*([０-９\d]{1,2})\s*月末", label)
    if not m:
        return None
    y = m.group(1).translate(ZEN)
    mo = m.group(2).translate(ZEN)
    return f"{y}{int(mo):02d}"


def discover(client) -> list[dict]:
    """从公表页解析出所有下载项。ID 每期会变，所以不缓存不写死。"""
    resp = fetch.get(PAGE)
    text = html.unescape(resp.text)
    out: list[dict] = []
    for url, inner in re.findall(
        r'<a[^>]*href="([^"]*FileDownload[^"]*)"[^>]*>(.*?)</a>', text, re.S
    ):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner)).strip()
        m = TABLE_RE.match(label)
        if not m:
            continue
        out.append({
            "table": m.group(1),
            "period": parse_period(label) or "unknown",
            "label": label,
            "file_id": url.split("file=")[-1],
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="FIT/FIP 認定・導入量 ingest")
    ap.add_argument("--table", choices=["A", "B", "C"], help="只取某张表")
    ap.add_argument("--latest", type=int, help="只取最近 N 期")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    fetch.setup_logging()

    client = fetch.new_session()
    try:
        items = discover(client)
    finally:
        client.close()

    if not items:
        log.error("公表页没解析出任何下载项 —— 页面结构可能变了，先人工打开 %s", PAGE)
        return

    by_table: dict[str, int] = {}
    for it in items:
        by_table[it["table"]] = by_table.get(it["table"], 0) + 1
    log.info("公表页解析到 %d 项：%s", len(items), by_table)

    if args.table:
        items = [i for i in items if i["table"] == args.table]
    if args.latest:
        keep: dict[str, list] = {}
        for i in sorted(items, key=lambda x: x["period"], reverse=True):
            keep.setdefault(i["table"], [])
            if len(keep[i["table"]]) < args.latest:
                keep[i["table"]].append(i)
        items = [i for v in keep.values() for i in v]

    if args.dry_run:
        for i in sorted(items, key=lambda x: (x["table"], x["period"])):
            log.info("  %s表 %s  %s", i["table"], i["period"], i["file_id"])
        log.info("dry-run —— 共 %d 个，落盘 %s", len(items), paths.raw("fit"))
        return

    got = skipped = failed = 0
    periods: set[str] = set()
    for it in sorted(items, key=lambda x: (x["table"], x["period"])):
        name = f"{it['table']}_{it['period']}.xlsx"   # 老期号会在下面改成 .xls
        dest = paths.raw("fit", it["table"]) / name
        if dest.is_file() and dest.stat().st_size > 0 and not args.force:
            skipped += 1
            periods.add(it["period"])
            continue
        try:
            resp = fetch.get(f"{DL}{it['file_id']}")
        except Exception as exc:  # noqa: BLE001 —— 单个失败不中断
            log.warning("%s → %s", name, type(exc).__name__)
            failed += 1
            continue
        body = resp.content
        # xlsx = zip（PK）；2018-03 及更早是老式 xls = OLE 复合文档（D0 CF 11 E0）。
        # 早先只认 PK，把 2017-09/12・2018-03 三期全拒了 —— 那是真数据不是错误页。
        is_xlsx = body[:2] == b"PK"
        is_xls = body[:4] == b"\xd0\xcf\x11\xe0"
        if not (is_xlsx or is_xls):
            log.warning("%s 既不是 xlsx 也不是 xls（%d 字节），跳过", name, len(body))
            failed += 1
            continue
        if is_xls:
            dest = dest.with_suffix(".xls")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        got += 1
        periods.add(it["period"])
        log.info("%s (%s)", name, report.human_bytes(len(body)))

    total = paths.dir_size(paths.raw("fit"))
    ps = sorted(p for p in periods if p != "unknown")
    log.info("新取 %d / 跳过 %d / 失败 %d；累计 %s", got, skipped, failed,
             report.human_bytes(total))

    report.emit(
        path=str(paths.raw("fit")),
        bytes=total,
        size_human=report.human_bytes(total),
        files=got + skipped,
        fetched_now=got,
        skipped_existing=skipped,
        failed=failed,
        time_range=f"{ps[0]}–{ps[-1]}" if ps else None,
        tables=by_table,
        has_coordinates=False,      # ⚠️ 市町村级聚合，不是逐设施
        granularity="市町村（B表）/ 都道府県（A表）× 电源种 × 容量档，季度",
        licence="TO VERIFY —— 経産省 公表資料",
        source_url=PAGE,
    )


if __name__ == "__main__":
    main()
