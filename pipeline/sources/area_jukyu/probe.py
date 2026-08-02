"""10 社エリア需給実績 的下载入口探测器。

各社页面结构本来就不统一，还会因改版换 URL。
既是 ingest 实装的前置调查，也是将来「突然取不到了」时的排查工具。

用法：
    python -m pipeline.sources.area_jukyu.probe            # 全社
    python -m pipeline.sources.area_jukyu.probe --utility tepco
"""

from __future__ import annotations

import argparse
import logging
import re
from urllib.parse import urljoin

from pipeline.core import fetch

log = logging.getLogger("powerjp.area_jukyu.probe")

# 各社 エリア需給実績（過去実績ダウンロード）页面候选。
# 每社给多个候选，全部都探（只取第一个 200 会漏掉归档页）。
CANDIDATES: dict[str, list[str]] = {
    "hokkaido": [
        "https://www.hepco.co.jp/network/con_service/public_document/supply_demand_results/",
    ],
    "tohoku": [
        "https://setsuden.nw.tohoku-epco.co.jp/download.html",
        "https://setsuden.nw.tohoku-epco.co.jp/",
    ],
    "tokyo": [
        "https://www.tepco.co.jp/forecast/html/area_jukyu-j.html",
        "https://www.tepco.co.jp/forecast/html/area_jukyu_p-j.html",
    ],
    "chubu": [
        "https://powergrid.chuden.co.jp/service_solution/toukei_data/area/",
        "https://powergrid.chuden.co.jp/denkiyoho/",
    ],
    "hokuriku": [
        "https://www.rikuden.co.jp/nw_jyukyudata/area_jisseki.html",
    ],
    "kansai": [
        "https://www.kansai-td.co.jp/denkiyoho/area-performance/past.html",
        "https://www.kansai-td.co.jp/denkiyoho/download/",
    ],
    "chugoku": [
        "https://www.energia.co.jp/nw/service/retailer/data/area/",
        "https://www.energia.co.jp/nw/jukyuu/eria_jukyu.html",
        "https://www.energia.co.jp/nw/jukyuu/download.html",
    ],
    "shikoku": [
        "https://www.yonden.co.jp/nw/supply_demand/index.html",
    ],
    "kyushu": [
        "https://www.kyuden.co.jp/td_area_jukyu/jukyu.html",
        "https://www.kyuden.co.jp/td_power_usages/jukyuHistory_list.html",
    ],
    "okinawa": [
        "https://www.okiden.co.jp/business-support/service/supply-and-demand/",
        "https://www.okiden.co.jp/denki2/",
    ],
}

DATA_EXT = re.compile(r"\.(csv|zip|xlsx?|CSV|ZIP)(\?|$)")


def probe_one(utility: str, urls: list[str]) -> list[dict]:
    """全候选都探 —— 只取第一个 200 会漏掉「当日速報页有、归档页才是全量」的情况。"""
    results: list[dict] = []
    for url in urls:
        try:
            resp = fetch.get(url, delay=3.0)
        except Exception as exc:  # noqa: BLE001 —— 探测阶段任何失败只是「这个候选不行」
            log.warning("[%s] %s → %s", utility, url, type(exc).__name__)
            results.append({"page": url, "status": "ERROR", "error": type(exc).__name__,
                            "data_links": []})
            continue

        html = resp.text
        links = {
            urljoin(str(resp.url), m.group(1))
            for m in re.finditer(r'href=["\']([^"\']+)["\']', html)
        }
        data_links = sorted(u for u in links if DATA_EXT.search(u))
        log.info("[%s] %s → HTTP %d, %d 链接, 数据文件 %d 个",
                 utility, url, resp.status_code, len(links), len(data_links))
        results.append({
            "page": str(resp.url),
            "status": resp.status_code,
            "n_links": len(links),
            "data_links": data_links,
        })
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="10 社需给实绩下载入口探测")
    ap.add_argument("--utility", help="只探一社")
    args = ap.parse_args()

    fetch.setup_logging()
    targets = {args.utility: CANDIDATES[args.utility]} if args.utility else CANDIDATES

    for utility, urls in targets.items():
        print(f"\n===== {utility} =====")
        for r in probe_one(utility, urls):
            print(f"  page: {r['page']}  status: {r['status']}  "
                  f"数据文件 {len(r['data_links'])} 个")
            # 同一目录的大量同构链接只示 3 个样本 + 计数，避免刷屏
            by_dir: dict[str, list[str]] = {}
            for link in r["data_links"]:
                by_dir.setdefault(link.rsplit("/", 1)[0], []).append(link)
            for d, items in sorted(by_dir.items()):
                names = sorted(i.rsplit("/", 1)[1] for i in items)
                sample = names[:3] + (["…"] if len(names) > 6 else []) + names[-3:] \
                    if len(names) > 6 else names
                print(f"    {d}/  ({len(names)} 个)")
                print(f"      {', '.join(sample)}")


if __name__ == "__main__":
    main()
