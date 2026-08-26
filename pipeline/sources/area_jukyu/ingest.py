"""エリア需給実績 10 社 ingest（type: auto）。

## 两种格式并存

**FY2024 以降**：全国统一命名 `eria_jukyu_{YYYYMM}_{NN}.csv`（NN = 事业者代码
01北海道 02東北 03東京 04中部 05北陸 06関西 07中国 08四国 09九州 10沖縄）。
各社只有存放目录不同 → 按月枚举 URL 即可。

**FY2023 以前**：各社各不相同（年度别 / 四半期别，文件名和命名规则都是两码事）。
→ 从归档页面刮 .csv 链接。

各社的下载入口是用 `probe.py` 定位的（网站改版后先跑 probe）。
部分社的归档页面是 JS 驱动、静态链接取不到 → 那些社的旧格式仍未取得。
缺了什么会出现在 metadata 的 `legacy_missing` 里。

用法：
    python -m pipeline.sources.area_jukyu.ingest                    # 全社・全期間
    python -m pipeline.sources.area_jukyu.ingest --utility tokyo
    python -m pipeline.sources.area_jukyu.ingest --skip-legacy
    python -m pipeline.sources.area_jukyu.ingest --dry-run
"""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin

import httpx

from pipeline.core import fetch, paths, report

log = logging.getLogger("powerjp.area_jukyu")


@dataclass
class Utility:
    code: str                      # 事业者代码 NN
    name_ja: str
    unified_base: str | None       # eria_jukyu_YYYYMM_NN.csv 的存放目录（结尾带 /）
    unified_from: tuple[int, int]  # 统一格式的起始 (年, 月)。不确定就往早了放，容许 404
    legacy_pages: list[str] = field(default_factory=list)  # 归档页面（刮 .csv 链接）
    legacy_pattern: str | None = None   # 收窄目标的正则（None 则全部 .csv）
    referer: str | None = None
    # 部分社的归档清单只在 JSON API 里（HTML 中没有链接）
    index_json: str | None = None
    index_pattern: str | None = None


UTILITIES: dict[str, Utility] = {
    "hokkaido": Utility(
        code="01", name_ja="北海道電力ネットワーク",
        unified_base="https://www.hepco.co.jp/network/con_service/public_document/supply_demand_results/csv/",
        unified_from=(2024, 4),
        legacy_pages=["https://www.hepco.co.jp/network/con_service/public_document/supply_demand_results/"],
        legacy_pattern=r"sup_dem_results_",
    ),
    "tohoku": Utility(
        code="02", name_ja="東北電力ネットワーク",
        unified_base="https://setsuden.nw.tohoku-epco.co.jp/common/demand/",
        unified_from=(2024, 2),
        legacy_pages=["https://setsuden.nw.tohoku-epco.co.jp/download.html"],
        legacy_pattern=r"juyo_\d{4}_tohoku",
    ),
    "tokyo": Utility(
        code="03", name_ja="東京電力パワーグリッド",
        unified_base="https://www.tepco.co.jp/forecast/html/images/",
        unified_from=(2024, 2),
        legacy_pages=["https://www.tepco.co.jp/forecast/html/area_jukyu-j.html"],
        legacy_pattern=r"(area|juyo)[-_]",
    ),
    "chubu": Utility(
        code="04", name_ja="中部電力パワーグリッド",
        # denki_yoho_content_data/ 下**只放当月和前月**。
        # 过去分是年度 zip（eria_jukyu_{FY}.zip）和年别 CSV，
        # 清单只出现在 getFilesInfo.php 这个 JSON API 里。
        unified_base="https://powergrid.chuden.co.jp/denki_yoho_content_data/",
        unified_from=(2026, 6),
        index_json="https://powergrid.chuden.co.jp/denkiyoho/resource/php/getFilesInfo.php",
        index_pattern=r"(eria_jukyu_\d{4}\.zip|areabalance)",
        referer="https://powergrid.chuden.co.jp/denkiyoho/eriajukyu_data/",
    ),
    "hokuriku": Utility(
        code="05", name_ja="北陸電力送配電",
        unified_base="https://www.rikuden.co.jp/nw/denki-yoho/csv/",
        unified_from=(2024, 4),
        legacy_pages=["https://www.rikuden.co.jp/nw_jyukyudata/area_jisseki.html"],
        legacy_pattern=r"area_jisseki_rikuden",
    ),
    "kansai": Utility(
        code="06", name_ja="関西電力送配電",
        unified_base="https://www.kansai-td.co.jp/interchange/denkiyoho/area-performance/",
        unified_from=(2024, 4),
        legacy_pages=["https://www.kansai-td.co.jp/denkiyoho/area-performance/past.html"],
        legacy_pattern=r"area_jyukyu_jisseki_\d{4}",
        referer="https://www.kansai-td.co.jp/denkiyoho/area-performance/index.html",
    ),
    "chugoku": Utility(
        code="07", name_ja="中国電力ネットワーク",
        unified_base="https://www.energia.co.jp/nw/jukyuu/sys/",
        unified_from=(2024, 1),  # 该社 JS 里写的是 min_y=2024
        legacy_pages=["https://www.energia.co.jp/nw/service/retailer/data/area/"],
        legacy_pattern=r"kako-\d{4}",
        referer="https://www.energia.co.jp/nw/jukyuu/eria_jukyu.html",
    ),
    "shikoku": Utility(
        code="08", name_ja="四国電力送配電",
        unified_base="https://www.yonden.co.jp/nw/supply_demand/csv/",
        unified_from=(2024, 4),
        legacy_pages=[],  # 归档页面未找到（见 INGEST.md 已知坑）
    ),
    "kyushu": Utility(
        code="09", name_ja="九州電力送配電",
        unified_base="https://www.kyuden.co.jp/td_area_jukyu/csv/",
        unified_from=(2024, 4),
        legacy_pages=["https://www.kyuden.co.jp/td_area_jukyu/jukyu.html"],
        legacy_pattern=r"area_jyukyu_jisseki_",
    ),
    "okinawa": Utility(
        code="10", name_ja="沖縄電力",
        unified_base="https://www.okiden.co.jp/business-support/service/supply-and-demand/csv/",
        unified_from=(2024, 2),
        legacy_pages=["https://www.okiden.co.jp/business-support/service/supply-and-demand/"],
        legacy_pattern=r"/jukyu/csv/\d{4}\.csv",
    ),
}

# 统一格式连续这么多次 404 就打断该社，不再往下枚举
MAX_CONSECUTIVE_MISS = 4


def months_since(start: tuple[int, int]) -> list[tuple[int, int]]:
    today = date.today()
    y, m = start
    out: list[tuple[int, int]] = []
    while (y, m) <= (today.year, today.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def save(content: bytes, utility: str, filename: str) -> int:
    dest = paths.raw("area_jukyu", utility) / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return len(content)


def exists(utility: str, filename: str) -> bool:
    p = paths.raw("area_jukyu", utility) / filename
    return p.is_file() and p.stat().st_size > 0


def fetch_unified(key: str, u: Utility, *, force: bool, recent: int = 0) -> dict:
    """按月枚举统一格式并下载。

    `recent > 0` 时，**最近 N 个月（含当月）即使已存在也强刷** —— 当月文件是
    滚动累积的，「已存在就跳过」会让它停在首次取得的那天（OCCTO 当日文件同款坑，
    见 orchestration/schedules.py）。跨月边界多带一个月，上月最终版也能收敛。
    """
    if not u.unified_base:
        return {"fetched": [], "missing": [], "bytes": 0}
    headers = {"Referer": u.referer} if u.referer else None
    got: list[str] = []
    missing: list[str] = []
    total = 0
    consecutive_miss = 0

    months = months_since(u.unified_from)
    recent_set = set(months[-recent:]) if recent > 0 else set()
    for y, m in months:
        name = f"eria_jukyu_{y}{m:02d}_{u.code}.csv"
        if exists(key, name) and not force and (y, m) not in recent_set:
            got.append(name)
            consecutive_miss = 0
            continue
        url = urljoin(u.unified_base, name)
        try:
            resp = fetch.get(url, headers=headers)
        except httpx.HTTPStatusError:
            missing.append(name)
            consecutive_miss += 1
            if consecutive_miss >= MAX_CONSECUTIVE_MISS:
                log.info("[%s] 连续 %d 件缺失，打断（%s 之后不再试）",
                         key, consecutive_miss, name)
                break
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] %s → %s", key, name, type(exc).__name__)
            missing.append(name)
            consecutive_miss += 1
            continue

        if not resp.content or len(resp.content) < 200:
            # 返回 200 但内容为空/极小 = 未公开的月份。这个站群的典型失败模式。
            missing.append(name)
            consecutive_miss += 1
            continue
        total += save(resp.content, key, name)
        got.append(name)
        consecutive_miss = 0
        log.info("[%s] %s (%s)", key, name, report.human_bytes(len(resp.content)))

    return {"fetched": got, "missing": missing, "bytes": total}


def _index_json_links(u: Utility) -> set[str]:
    """从归档清单走 JSON API 的社（如中部）取链接。

    返回的是 `{"path": "/denki_yoho_content_data/xxx.csv", ...}` 这样的数组。
    """
    if not u.index_json:
        return set()
    headers = {"Referer": u.referer} if u.referer else None
    try:
        resp = fetch.get(u.index_json, headers=headers)
        items = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("index JSON %s → %s", u.index_json, type(exc).__name__)
        return set()
    if not isinstance(items, list):
        log.warning("index JSON 的结构不符预期（不是 list）")
        return set()

    out: set[str] = set()
    for item in items:
        path = str(item.get("path", "")) if isinstance(item, dict) else ""
        if not path:
            continue
        if u.index_pattern and not re.search(u.index_pattern, path):
            continue
        out.add(urljoin(u.index_json, path))
    log.info("index JSON 中取得 %d 件", len(out))
    return out


def fetch_legacy(key: str, u: Utility, *, force: bool) -> dict:
    """从归档页面 / index JSON 收集过去分的链接并下载。"""
    if not u.legacy_pages and not u.index_json:
        return {"fetched": [], "bytes": 0, "note": "归档页面未找到"}

    links: set[str] = _index_json_links(u)
    for page in u.legacy_pages:
        try:
            resp = fetch.get(page)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] 归档页 %s → %s", key, page, type(exc).__name__)
            continue
        for m in re.finditer(r'href=["\']([^"\']+\.csv)["\']', resp.text, re.I):
            absolute = urljoin(str(resp.url), m.group(1))
            if u.legacy_pattern and not re.search(u.legacy_pattern, absolute):
                continue
            # 统一格式由 unified 那边取，这里排除
            if "eria_jukyu_" in absolute:
                continue
            links.add(absolute)

    got: list[str] = []
    total = 0
    for url in sorted(links):
        name = url.rsplit("/", 1)[1]
        if exists(key, name) and not force:
            got.append(name)
            continue
        ref = u.referer or (u.legacy_pages[0] if u.legacy_pages else None)
        try:
            resp = fetch.get(url, headers={"Referer": ref} if ref else None)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] %s → %s", key, name, type(exc).__name__)
            continue
        if not resp.content:
            continue
        total += save(resp.content, key, name)
        got.append(name)
        log.info("[%s] (legacy) %s (%s)", key, name, report.human_bytes(len(resp.content)))
    return {"fetched": got, "bytes": total}


def main() -> None:
    ap = argparse.ArgumentParser(description="エリア需給実績 10 社 ingest")
    ap.add_argument("--utility", choices=sorted(UTILITIES))
    ap.add_argument("--skip-legacy", action="store_true")
    ap.add_argument("--skip-unified", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--recent", type=int, default=0, metavar="N",
                    help="最近 N 个月（含当月）即使已存在也强刷，并跳过 legacy。"
                         "定时任务用（当月文件是滚动累积的）")
    args = ap.parse_args()
    if args.recent > 0:
        args.skip_legacy = True

    fetch.setup_logging()
    targets = {args.utility: UTILITIES[args.utility]} if args.utility else UTILITIES

    if args.dry_run:
        for key, u in targets.items():
            ms = months_since(u.unified_from)
            log.info("[%s] 统一格式 %d 个月（%d-%02d 起）＋ 归档页 %d 个",
                     key, len(ms), *u.unified_from, len(u.legacy_pages))
        return

    summary: dict[str, dict] = {}
    legacy_missing: list[str] = []

    for key, u in targets.items():
        log.info("===== %s（%s / code=%s）=====", key, u.name_ja, u.code)
        uni = {"fetched": [], "missing": [], "bytes": 0}
        leg = {"fetched": [], "bytes": 0}
        if not args.skip_unified:
            uni = fetch_unified(key, u, force=args.force, recent=args.recent)
        if not args.skip_legacy:
            leg = fetch_legacy(key, u, force=args.force)
            if leg.get("note"):
                legacy_missing.append(key)

        summary[key] = {
            "code": u.code,
            "unified_files": len(uni["fetched"]),
            "unified_missing": len(uni["missing"]),
            "legacy_files": len(leg["fetched"]),
            "bytes": uni["bytes"] + leg["bytes"],
            "dir_bytes": paths.dir_size(paths.raw("area_jukyu", key)),
        }
        log.info("[%s] 统一 %d 件 / 归档 %d 件 / 合计 %s", key,
                 len(uni["fetched"]), len(leg["fetched"]),
                 report.human_bytes(summary[key]["dir_bytes"]))

    total_bytes = paths.dir_size(paths.raw("area_jukyu"))
    total_files = sum(s["unified_files"] + s["legacy_files"] for s in summary.values())
    log.info("全体：%d 个文件 / %s", total_files, report.human_bytes(total_bytes))
    if legacy_missing:
        log.warning("归档页面未找到（FY2023 以前未取得）：%s", legacy_missing)

    report.emit(
        path=str(paths.raw("area_jukyu")),
        bytes=total_bytes,
        size_human=report.human_bytes(total_bytes),
        rows=None,
        files=total_files,
        by_utility=summary,
        legacy_missing=legacy_missing or None,
        licence="TO VERIFY —— 各社规约，10 社不统一",
        source_url="各社 でんき予報 / エリア需給実績 页面（见 probe.py）",
    )


if __name__ == "__main__":
    main()
