"""OCCTO 連系線運用容量 ingest（type: auto）。

OCCTO 的 news 列表是 JS 驱动，静态链接取不到。但资产 URL 有规律，
所以按年度枚举直接请求：

    /assets/renkeisenriyou/oshirase/{FY}/files/oshirase_{n}_{...}.pdf

FY2023 起命名才稳定（更早的是另一套命名，v0 不追）。
一个年度公告下挂 5 个 PDF：

    oshirase_1_{FY+1}-{FY+10}_unyouyouryou.pdf   ← 主目标：10 年分的运用容量
    oshirase_2_{FY}_sansyutsuhouhoukekka.pdf     算出方法与结果
    oshirase_3_{FY}_setsubiteishiji.pdf          设备停止时
    oshirase_4_{FY}_minaoshi.pdf                 修订
    oshirase_5_{FY}_sagyouteishi_eikyou.pdf      作业停止的影响

⚠️ 只把 PDF 原样放进 raw。表格抽取是 structured 阶段的事（未实装）。

用法：
    python -m pipeline.sources.occto_capacity.ingest
    python -m pipeline.sources.occto_capacity.ingest --fy 2025
    python -m pipeline.sources.occto_capacity.ingest --dry-run
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

import httpx

from pipeline.core import fetch, paths, report

BASE = "https://www.occto.or.jp/assets/renkeisenriyou/oshirase"
FIRST_FY = 2023  # 更早的年度命名不同

log = logging.getLogger("powerjp.occto_capacity")


def pdf_names(fy: int) -> dict[str, str]:
    return {
        "unyouyouryou": f"oshirase_1_{fy + 1}-{fy + 10}_unyouyouryou.pdf",
        "sansyutsuhouhoukekka": f"oshirase_2_{fy}_sansyutsuhouhoukekka.pdf",
        "setsubiteishiji": f"oshirase_3_{fy}_setsubiteishiji.pdf",
        "minaoshi": f"oshirase_4_{fy}_minaoshi.pdf",
        "sagyouteishi_eikyou": f"oshirase_5_{fy}_sagyouteishi_eikyou.pdf",
    }


def fetch_fy(fy: int, *, force: bool) -> dict:
    out_dir = paths.raw("occto_capacity", str(fy))
    got: dict[str, int] = {}
    for kind, name in pdf_names(fy).items():
        dest = out_dir / name
        if dest.is_file() and dest.stat().st_size > 0 and not force:
            got[kind] = dest.stat().st_size
            continue
        url = f"{BASE}/{fy}/files/{name}"
        try:
            resp = fetch.get(url)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            log.info("FY%d %s → HTTP %s", fy, kind, code)
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("FY%d %s → %s", fy, kind, type(exc).__name__)
            continue
        if not resp.content.startswith(b"%PDF"):
            log.warning("FY%d %s：返回的不是 PDF，丢弃", fy, kind)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        got[kind] = len(resp.content)
        log.info("FY%d %s (%s)", fy, name, report.human_bytes(len(resp.content)))
    return got


def main() -> None:
    ap = argparse.ArgumentParser(description="OCCTO 連系線運用容量 ingest")
    ap.add_argument("--fy", type=int, help="只取指定年度")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    fetch.setup_logging()
    latest_fy = date.today().year if date.today().month >= 4 else date.today().year - 1
    targets = [args.fy] if args.fy else list(range(FIRST_FY, latest_fy + 1))

    if args.dry_run:
        for fy in targets:
            log.info("FY%d → %s", fy, list(pdf_names(fy).values()))
        return

    by_fy: dict[int, dict[str, int]] = {}
    for fy in targets:
        got = fetch_fy(fy, force=args.force)
        if got:
            by_fy[fy] = got

    size = paths.dir_size(paths.raw("occto_capacity"))
    fys = sorted(by_fy)
    log.info("取得 %d 个年度（%s），合计 %s", len(by_fy),
             ", ".join(f"FY{f}" for f in fys), report.human_bytes(size))
    if not by_fy:
        log.warning("一个都没取到 —— URL 规则可能变了。手工开一个确认")

    report.emit(
        path=str(paths.raw("occto_capacity")),
        bytes=size,
        size_human=report.human_bytes(size),
        fiscal_years=fys,
        files=sum(len(v) for v in by_fy.values()),
        by_fy={str(k): v for k, v in by_fy.items()},
        format="PDF（未解析。表格抽取在 structured 阶段）",
        licence="TO VERIFY —— OCCTO 公表資料",
        source_url="https://www.occto.or.jp/renkeisenriyou/oshirase/",
    )


if __name__ == "__main__":
    main()
