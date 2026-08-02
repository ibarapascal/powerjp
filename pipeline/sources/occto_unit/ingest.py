"""OCCTO ユニット別発電実績公開システム ingest（type: auto）。

## 这是什么

**逐机组（ユニット）× 30 分 × 48 断面/日 的発電実績。**
立项调研笔记里写着「逐机组出力确认不公开」—— 那是错的，见 INGEST.md。

一次请求就能拿到「全区域 × 全电源种 × 单日」的全部机组（约 471 条 / 284 発電所）。

## 调用流程（三步）

    1. GET  /hks-web-public/info/home                未同意时 302 → 免責同意页，同时拿到 session
    2. POST /hks-web-public/disclaimer-agree/next     agreed=0
    3. POST /hks-web-public/info/hks/search           → JSON

⚠️ **第 2 步的值是 `agreed=0` 不是 `1`**。页面 JS（HKSHS004.js）的 toggle 逻辑是：
载入时隐藏字段被置为 `'1'`（未勾选），勾选后才变成 `'0'`。反直觉，但实测只有 0 通过。

`info/hks/downloadCsv` 端点存在但 POST 返回 405；不需要它 ——
`search` 返回的 JSON 已经是全量结构化数据，比 CSV 更好用。

## 边界

- **2024-03-25 起**。2024-03-24 以前是试验数据，不可用。
- 每日 15:30 前后更新（JSON 里的 `upyh` 字段就是该批次的更新时刻）
- 值是**速报値**，系统自己声明可能缺数据
- 与 JEPX 的 HJKS 会有乖离（计测值 vs 申告值），两者不要互相「校正」
- 只有火力/原子力/水力/その他 有机组级条目；風力・太陽光 没有

用法：
    python -m pipeline.sources.occto_unit.ingest                 # 最近 7 天
    python -m pipeline.sources.occto_unit.ingest --days 30
    python -m pipeline.sources.occto_unit.ingest --date 20260730
    python -m pipeline.sources.occto_unit.ingest --from 20240325 --to 20241231
    python -m pipeline.sources.occto_unit.ingest --all           # 2024-03-25 至今
    python -m pipeline.sources.occto_unit.ingest --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta

import httpx

from pipeline.core import fetch, paths, report

BASE = "https://hatsuden-kokai.occto.or.jp/hks-web-public"
HOME_URL = f"{BASE}/info/home"
AGREE_URL = f"{BASE}/disclaimer-agree/next"
SEARCH_URL = f"{BASE}/info/hks/search"
UNIT_PAGE = f"{BASE}/info/hks"

FIRST_DATE = date(2024, 3, 25)  # 更早的是试验数据

# 表单里 99 = すべて，所以区域和电源种各一次全选即可
ALL_AREAS = "99"
ALL_FUELS = "99"

# 检索表单上标着 data-recordcountproperty="1000"。单日约 471 条，安全；
# 但按区间查会成倍增长并可能被这个上限截断，所以**一天一次**，不做区间查询。
RECORD_LIMIT_HINT = 1000

log = logging.getLogger("powerjp.occto_unit")

# 何日連続で空だったら session を貼り直すか。
# 実在する連続欠測（年末年始など）で無駄に貼り直さない程度に大きく、
# 切れたまま何時間も空振りしない程度に小さく。
RELOGIN_AFTER_EMPTY = 5


def open_session() -> httpx.Client:
    """建 session 并通过免責同意画面。"""
    client = fetch.new_session()
    # 先摸一次首页拿 session cookie（未同意时会 302 到同意页，这是正常的）
    client.get(HOME_URL)
    fetch.post(client, AGREE_URL, data={"agreed": "0"})
    log.debug("免責同意已通过")
    return client


def fetch_day(client: httpx.Client, day: date) -> tuple[bytes, int] | None:
    """取一天的全部机组。返回 (原始 JSON 字节, 条数)。"""
    ymd = day.strftime("%Y/%m/%d")
    data = {
        "htdnsCd": "",
        "htdnsNm": "",
        "unitNm": "",
        "areaCheckbox": ALL_AREAS,
        "hatudenHosikiCheckbox": ALL_FUELS,
        "tgtDateDateFrom": ymd,
        "tgtDateDateTo": ymd,
    }
    resp = fetch.post(client, SEARCH_URL, data=data,
                      headers={"Referer": UNIT_PAGE})
    body = resp.content
    try:
        parsed = json.loads(body)
        items = parsed.get("data", {}).get("items") or []
    except (ValueError, AttributeError):
        log.warning("[%s] 响应不是预期的 JSON（%d 字节）", day, len(body))
        return None
    if not items:
        return None
    if len(items) >= RECORD_LIMIT_HINT:
        log.warning("[%s] 条数 %d 触到上限提示值 %d —— 可能被截断，需要拆查询",
                    day, len(items), RECORD_LIMIT_HINT)
    return body, len(items)


def dest_for(day: date):
    return paths.raw("occto_unit", day.strftime("%Y-%m")) / f"unit_{day:%Y%m%d}.json"


def daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def main() -> None:
    ap = argparse.ArgumentParser(description="OCCTO ユニット別発電実績 ingest")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="2024-03-25 至今全量")
    g.add_argument("--date", help="单日 YYYYMMDD")
    g.add_argument("--days", type=int, default=7, help="最近 N 天（默认 7）")
    ap.add_argument("--from", dest="date_from", help="起始日 YYYYMMDD")
    ap.add_argument("--to", dest="date_to", help="结束日 YYYYMMDD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    fetch.setup_logging()

    # 当日 15:30 才更新，默认不碰今天
    yesterday = date.today() - timedelta(days=1)
    if args.date:
        days = [datetime.strptime(args.date, "%Y%m%d").date()]
    elif args.date_from:
        start = datetime.strptime(args.date_from, "%Y%m%d").date()
        end = datetime.strptime(args.date_to, "%Y%m%d").date() if args.date_to else yesterday
        days = daterange(start, end)
    elif args.all:
        days = daterange(FIRST_DATE, yesterday)
    else:
        days = daterange(yesterday - timedelta(days=args.days - 1), yesterday)

    days = [d for d in days if d >= FIRST_DATE]
    if not days:
        log.warning("目标日期全部早于 %s（试验数据期），无事可做", FIRST_DATE)
        return

    est_min = len(days) * fetch.DEFAULT_DELAY / 60
    log.info("计划：%d 天（%s … %s），每天 1 次请求，预估 %.0f 分钟",
             len(days), days[0], days[-1], est_min)
    if args.dry_run:
        log.info("dry-run —— 落盘 %s", paths.raw("occto_unit"))
        return

    got = skipped = empty = 0
    written = 0
    total_items = 0
    consecutive_empty = 0
    # 不能用 `with open_session()` —— open_session 内部已经发过请求，
    # httpx.Client 被打开后再进上下文管理器会抛 "Cannot open a client instance more than once"
    client = open_session()
    try:
        for day in days:
            dest = dest_for(day)
            if dest.is_file() and dest.stat().st_size > 0 and not args.force:
                skipped += 1
                continue
            try:
                result = fetch_day(client, day)
            except Exception as exc:  # noqa: BLE001 —— 单日失败不中断整批
                log.warning("[%s] %s", day, type(exc).__name__)
                continue
            if result is None:
                empty += 1
                # ⚠️ 「本当に無データ」と「session 切れ」はどちらも None で返る。
                #    長時間の回填では後者が必ず起きるので、連続したら貼り直す。
                #    これが無いと切れた後は延々と空振りし、
                #    「無データの日が大量にある」ように見える（occto_flow で実際に起きた）。
                consecutive_empty += 1
                if consecutive_empty >= RELOGIN_AFTER_EMPTY:
                    log.info("%d 日連続で空 —— session を貼り直す", consecutive_empty)
                    client.close()
                    client = open_session()
                    consecutive_empty = 0
                continue
            consecutive_empty = 0
            body, n = result
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)  # 原始 JSON 原样落盘
            got += 1
            written += len(body)
            total_items += n
            log.info("[%s] %d 条机组 / %s", day, n, report.human_bytes(len(body)))
    finally:
        client.close()

    total = paths.dir_size(paths.raw("occto_unit"))
    log.info("新取 %d 天 / 跳过 %d / 无数据 %d；本次写入 %s（%d 条），累计 %s",
             got, skipped, empty, report.human_bytes(written), total_items,
             report.human_bytes(total))

    report.emit(
        path=str(paths.raw("occto_unit")),
        bytes=total,
        size_human=report.human_bytes(total),
        files=got + skipped,
        fetched_now=got,
        skipped_existing=skipped,
        no_data=empty,
        rows=total_items,
        time_range=f"{days[0]}–{days[-1]}",
        granularity="机组 × 30 分 × 48 断面/日",
        format="JSON（原样落盘）",
        licence="OCCTO 利用条件：可自由使用，须注明出处；经编辑加工须另行注明",
        source_url=UNIT_PAGE,
    )


if __name__ == "__main__":
    main()
