"""OCCTO 系統情報サービス 地内基幹送電線潮流実績 ingest（type: **auto**）。

## 不需要账号

立项时以为要注册 occtonet3，**实测是错的**。系統情報サービス的「公表」部分
用的是系统自动分配的匿名身份（ユーザID `RP11SSO` / ユーザ名「公表 利用者」），
URL 路径就是 `/public/`。POST 一次 LOGIN 端点拿到 session cookie 即可，
不需要任何凭据。

## 调用流程（四步）

    1. POST  …/SD/LOGIN_login          空 body，拿 session cookie
    2. POST  …/SD/CB01S020C            actionSubType=printOK + 头 `sdReqType: AJAX`
                                       → JSON，取 downloadKey / requestToken
    3. POST  …/SD/CB01S020C            actionSubType=download + 上一步两个 token
                                       → CSV（Shift_JIS）
    4. POST  …/SD/LOGOUT               释放 session

第 2 步的 token 是一次性的：每个「区域 × 日期」都要重新走一遍 2→3。
session（第 1 步）可以复用，所以一次 login 能批量取多天。

协议来源：`thgcMtdh/grid-api` 的 PHP 实现（powerflowmap 的后端）。
⚠️ 那个仓库**没有 license**，因此这里只采用「接口怎么调」这类事实，
代码为本项目独立编写。

## 边界

- 数据 2023-01 起（powerflowmap 可回放的范围）
- 每 30 分钟更新，服务器侧有约 2 分钟时滞
- 当日数据要到 23:32 之后才齐；未到齐就抓会拿到不完整的一天

用法：
    python -m pipeline.sources.occto_flow.ingest                    # 最近 7 天
    python -m pipeline.sources.occto_flow.ingest --days 30
    python -m pipeline.sources.occto_flow.ingest --date 20260731
    python -m pipeline.sources.occto_flow.ingest --from 20250401 --to 20250430
    python -m pipeline.sources.occto_flow.ingest --all              # 2023-01-01 至今
    python -m pipeline.sources.occto_flow.ingest --area 3 --days 3
    python -m pipeline.sources.occto_flow.ingest --dry-run
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta

import httpx

from pipeline.core import fetch, paths, report

BASE = "https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD"
LOGIN_URL = f"{BASE}/LOGIN_login"
DATA_URL = f"{BASE}/CB01S020C"
LOGOUT_URL = f"{BASE}/LOGOUT"

# 区域代码 → 名称。searchReqHdn 里要用日文名，服务端会核对。
AREAS: dict[int, str] = {
    1: "北海道", 2: "東北", 3: "東京", 4: "中部", 5: "北陸",
    6: "関西", 7: "中国", 8: "四国", 9: "九州", 10: "沖縄",
}

FIRST_DATE = date(2023, 1, 1)
AJAX_HEADERS = {"Accept": "application/json, text/javascript, */*; q=0.01",
                "sdReqType": "AJAX"}

log = logging.getLogger("powerjp.occto_flow")


def payload(area: int, day: date) -> dict[str, str]:
    """检索条件。`tgtDaHdn=00` 是全電圧（也可指定 500/275/220/187/132）。"""
    code = f"{area:02d}"
    return {
        "fwExtention.actionType": "reference",
        "fwExtention.actionSubType": "",
        "fwExtention.pathInfo": "CB01S020C",
        "fwExtention.formId": "CB01S020P",
        "requestToken": "",
        "tgtNngpHdn": day.strftime("%Y%m%d"),
        "downloadKey": "",
        "areaCdAreaSumNon": code,
        "tgtAreaHdn": code,
        "tgtDaHdn": "00",
        # 服务端会核对这串文字与上面的条件是否一致，格式必须照旧（逗号后的空格位置也是）
        "searchReqHdn": (
            f"[検索条件]対象年月日:{day.strftime('%Y/%m/%d')}, "
            f"対象エリア:{AREAS[area]},対象電圧:全電圧"
        ),
    }


# get_tokens が「session 切れ」を伝えるための番兵。
# `None`（データが無い日）と区別する必要がある —— 前者は貼り直して再試行、
# 後者は再試行しても無駄。
SESSION_LOST = ("__session_lost__", "")


def login(client: httpx.Client) -> None:
    fetch.post(client, LOGIN_URL, data={})
    log.debug("session 建立")


def logout(client: httpx.Client) -> None:
    try:
        fetch.post(client, LOGOUT_URL, data={}, retries=1)
    except Exception as exc:  # noqa: BLE001 —— 登出失败不影响已取到的数据
        log.debug("logout 失败（忽略）：%s", type(exc).__name__)


def get_tokens(client: httpx.Client, area: int, day: date) -> tuple[str, str] | None:
    """第 2 步：拿一次性的 downloadKey / requestToken。"""
    data = payload(area, day)
    data["fwExtention.actionSubType"] = "printOK"
    resp = fetch.post(client, DATA_URL, data=data, headers=AJAX_HEADERS)
    try:
        header = resp.json()["root"]["bizRoot"]["header"]
        key = header["downloadKey"]["value"]
        token = header["requestToken"]["value"]
    except (ValueError, KeyError, TypeError):
        # ⚠️ ここには**2 つの原因**が混ざる：
        #    ① session が切れて HTML のログイン画面が返った（長時間の回填で必ず起きる）
        #    ② 本当に応答が壊れている
        # 呼び出し側が session を貼り直して 1 度だけ再試行する。
        return SESSION_LOST
    if not key or not token:
        # 没有数据的日期（未来日 / 未公开）会返回空 token，不是错误
        return None
    return key, token


def fetch_one(client: httpx.Client, area: int, day: date, *, force: bool) -> int | None:
    """取一个「区域 × 日期」。返回写入字节数；跳过或无数据时返回 None。

    ## ⚠️ session が切れたら貼り直して 1 度だけ再試行する

    login は最初に 1 回だけ張るが、**数時間走ると必ず切れる**。
    切れた後は毎回ログイン画面が返るので、気づかないと
    「延々とリクエストを投げ続けて 1 件も取れない」状態になる。
    実測（2026-08-02 の回填）：2.5 時間走ったところで切れ、
    そこから 2 時間・3,489 回ぶんが全部無駄になった。
    """
    dest = paths.raw("occto_flow", day.strftime("%Y-%m")) / \
        f"chinai_kikan_jisseki_{day:%Y%m%d}_{area:02d}.csv"
    if dest.is_file() and dest.stat().st_size > 0 and not force:
        return None

    tokens = get_tokens(client, area, day)
    if tokens is SESSION_LOST:
        log.info("session が切れたので貼り直す（area=%d %s）", area, day)
        logout(client)
        login(client)
        tokens = get_tokens(client, area, day)
        if tokens is SESSION_LOST:
            # 貼り直しても駄目 = 本当に応答が壊れている
            log.warning("[area=%d %s] 再ログイン後も token を取れない", area, day)
            return None
    if tokens is None:
        return None
    key, token = tokens

    data = payload(area, day)
    data["fwExtention.actionSubType"] = "download"
    data["requestToken"] = token
    data["downloadKey"] = key
    resp = fetch.post(client, DATA_URL, data=data)

    body = resp.content
    if len(body) < 200 or body.lstrip()[:1] in (b"<", b"{"):
        # HTML / JSON 说明拿到的是错误页而不是 CSV
        log.warning("[area=%d %s] 返回的不是 CSV（%d 字节）", area, day, len(body))
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)  # Shift_JIS 原样落盘，转码是 Process 阶段的事
    return len(body)


def daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def main() -> None:
    ap = argparse.ArgumentParser(description="OCCTO 地内基幹送電線潮流実績 ingest")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="2023-01-01 至今全量")
    g.add_argument("--date", help="单日 YYYYMMDD")
    g.add_argument("--days", type=int, default=7, help="最近 N 天（默认 7）")
    ap.add_argument(
        "--include-today", action="store_true",
        help="让 --days 窗口包含今天（默认窗口结束于昨天）",
    )
    ap.add_argument("--from", dest="date_from", help="起始日 YYYYMMDD")
    ap.add_argument("--to", dest="date_to", help="结束日 YYYYMMDD")
    ap.add_argument("--area", type=int, choices=sorted(AREAS), help="只取一个区域")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    fetch.setup_logging()

    # 当日数据 23:32 之后才齐，默认不碰今天；30 分钟增量任务显式包含今天，
    # 用 force 反复更新当日文件，才能拿到新出现的断面。
    yesterday = date.today() - timedelta(days=1)
    window_end = date.today() if args.include_today else yesterday
    if args.date:
        days = [datetime.strptime(args.date, "%Y%m%d").date()]
    elif args.date_from:
        start = datetime.strptime(args.date_from, "%Y%m%d").date()
        end = datetime.strptime(args.date_to, "%Y%m%d").date() if args.date_to else window_end
        days = daterange(start, end)
    elif args.all:
        days = daterange(FIRST_DATE, window_end)
    else:
        days = daterange(window_end - timedelta(days=args.days - 1), window_end)

    areas = [args.area] if args.area else sorted(AREAS)
    total_requests = len(days) * len(areas) * 2  # 每格 2 次 POST（token + download）
    est_min = total_requests * fetch.DEFAULT_DELAY / 60

    log.info("计划：%d 天 × %d 区域 = %d 格，约 %d 次请求，预估 %.0f 分钟",
             len(days), len(areas), len(days) * len(areas), total_requests, est_min)
    if args.dry_run:
        log.info("dry-run —— %s … %s，落盘 %s",
                 days[0], days[-1], paths.raw("occto_flow"))
        return
    if est_min > 60:
        log.warning("预估超过 1 小时。已下载的会跳过，可以中断后重跑续传。")

    got = 0
    skipped = 0
    empty = 0
    written = 0
    with fetch.new_session() as client:
        login(client)
        try:
            for day in days:
                for area in areas:
                    dest = paths.raw("occto_flow", day.strftime("%Y-%m")) / \
                        f"chinai_kikan_jisseki_{day:%Y%m%d}_{area:02d}.csv"
                    if dest.is_file() and dest.stat().st_size > 0 and not args.force:
                        skipped += 1
                        continue
                    try:
                        size = fetch_one(client, area, day, force=args.force)
                    except Exception as exc:  # noqa: BLE001 —— 单格失败不中断整批
                        log.warning("[area=%d %s] %s", area, day, type(exc).__name__)
                        continue
                    if size is None:
                        empty += 1
                        continue
                    got += 1
                    written += size
                    log.info("[%s area=%02d] %s", day, area, report.human_bytes(size))
        finally:
            logout(client)

    total = paths.dir_size(paths.raw("occto_flow"))
    log.info("新取 %d 格 / 已存在跳过 %d / 无数据 %d；本次写入 %s，累计 %s",
             got, skipped, empty, report.human_bytes(written), report.human_bytes(total))

    report.emit(
        path=str(paths.raw("occto_flow")),
        bytes=total,
        size_human=report.human_bytes(total),
        files=got + skipped,
        fetched_now=got,
        skipped_existing=skipped,
        no_data=empty,
        time_range=f"{days[0]}–{days[-1]}",
        areas=areas,
        granularity="30 分 × 48 断面/日",
        encoding="Shift_JIS（原样落盘，不转码）",
        licence="OCCTO 利用条件：可自由使用，须注明出处；经编辑加工须另行注明",
        source_url="https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/LOGIN_login",
    )


if __name__ == "__main__":
    main()
