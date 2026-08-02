"""JEPOC 水力発電所データベース ingest（type: auto）。

電力土木技術協会（JEPOC）的水力发电所数据库。**会员非会员都可用，无需凭据。**

收录范围：**電気事業用是全部**水力発電所，自家用是最大出力 ≥1,000kW 的。

## ⚠️ 没有坐标

立项调研笔记写着「含坐标」——**实测是错的**。详细页的所在地字段只到
**都道府県 + 市町村**，没有经纬度。所以本源**补不了 AJG 的水力坐标缺口**。

它真正的价值在**水力建模参数**（AJG 和 GEM 都没有的）：

  発電形式   ダム水路式 / 水路式 / ダム式
  発電方式   流込み式 / 調整池式 / 貯水池式 / 揚水式   ← 三类资产的判别依据
  有効落差、最大使用水量、取水位、放水位、流域面積、水系名/河川名/級別

其中「発電方式」最关键：流込式 = 固定出力时序（像光伏）、調整池/貯水池式 = 带入流的
储能、揚水 = 储能对。把水力当一类资产建模是错的。

## 导航结构（三层 + 详细）

    POST index.php?_w=usData&_x=areashow2   AreaNo=01..10          → 水系列表
    POST index.php?_w=usData&_x=areashow3   AreaNo + RiverSystemNo → 発電所一覧
    GET  index.php?_w=usData&_x=detail2&pp=1&OwnerNo=&AreaNo=&RiverSys=&fd=  → 详细

全国共 **240 个水系**（北海道 24 / 東北 52 / 関東 16 / 中部 19 / 北陸 16 /
近畿 20 / 中国 30 / 四国 18 / 九州 44 / 沖縄 1）。

一览页已含：発電所名・事業者名・水系名・出力・発電形式・発電方式・運転開始年月。
详细页才有：有効落差・最大使用水量・取水位・放水位・流域面積・所在地市町村。

用法：
    python -m pipeline.sources.jepoc_hydro.ingest              # 只抓一览（约 250 请求）
    python -m pipeline.sources.jepoc_hydro.ingest --detail     # 连详细页一起（慢很多）
    python -m pipeline.sources.jepoc_hydro.ingest --area 1
    python -m pipeline.sources.jepoc_hydro.ingest --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import re

from pipeline.core import fetch, paths, report

BASE = "https://jepoc.or.jp/hydro/index.php"
AREAS = {1: "北海道", 2: "東北", 3: "関東", 4: "中部", 5: "北陸",
         6: "近畿", 7: "中国", 8: "四国", 9: "九州", 10: "沖縄"}

log = logging.getLogger("powerjp.jepoc_hydro")

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def text(html: str) -> str:
    return WS.sub(" ", TAG.sub("", html)).replace("&nbsp;", " ").strip()


def cells(row_html: str) -> list[str]:
    return [text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def river_systems(client, area: int) -> list[dict]:
    resp = fetch.post(client, f"{BASE}?_w=usData&_x=areashow2", data={"AreaNo": f"{area:02d}"})
    out = []
    for m in re.finditer(
        r'<input[^>]*name="RiverSystemNo"[^>]*value="(\d+)"[^>]*>\s*([^<\r\n]*)', resp.text
    ):
        label = m.group(2).strip()
        out.append({"no": m.group(1), "label": label})
    return out


def plants(client, area: int, river_no: str) -> list[dict]:
    resp = fetch.post(
        client, f"{BASE}?_w=usData&_x=areashow3",
        data={"AreaNo": f"{area:02d}", "RiverSystemNo": river_no},
    )
    html = resp.text
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        link = re.search(r'href\s*=\s*"([^"]*_x=detail2[^"]*)"', row)
        c = cells(row)
        if not link or len(c) < 7:
            continue
        out.append({
            "detail_url": link.group(1).replace("&amp;", "&"),
            "name": c[0], "operator": c[1], "river_system": c[2],
            "output_kw": c[3], "form": c[4], "method": c[5],
            "start_ym": c[6] if len(c) > 6 else "",
            "current_start_ym": c[7] if len(c) > 7 else "",
        })
    return out


def detail(client, url: str) -> dict:
    full = url if url.startswith("http") else f"https://jepoc.or.jp{url}"
    resp = fetch.get(full)
    fields: dict[str, str] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", resp.text, re.S):
        c = [x for x in cells(row) if x]
        if len(c) == 2:
            fields[c[0]] = c[1]
        elif len(c) == 3:                       # 項目名 | 単位 | データ
            fields[c[0]] = c[2]
            fields[f"{c[0]}_単位"] = c[1]
    return fields


def main() -> None:
    ap = argparse.ArgumentParser(description="JEPOC 水力発電所データベース ingest")
    ap.add_argument("--area", type=int, choices=sorted(AREAS))
    ap.add_argument("--detail", action="store_true", help="连详细页一起抓（慢很多）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    fetch.setup_logging()

    out_dir = paths.raw("jepoc_hydro")
    areas = [args.area] if args.area else sorted(AREAS)

    if args.dry_run:
        log.info("dry-run —— %d 个地方，先取水系列表再逐水系取一览；落盘 %s",
                 len(areas), out_dir)
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    all_plants: list[dict] = []
    systems_count = 0

    client = fetch.new_session()
    try:
        client.get("https://jepoc.or.jp/hydro/")           # 建 session
        for area in areas:
            systems = river_systems(client, area)
            systems_count += len(systems)
            log.info("[%s] %d 个水系", AREAS[area], len(systems))
            for sysm in systems:
                rows = plants(client, area, sysm["no"])
                for r in rows:
                    r.update(area_no=area, area_name=AREAS[area],
                             river_no=sysm["no"], river_label=sysm["label"])
                all_plants.extend(rows)
            log.info("[%s] 累计発電所 %d", AREAS[area], len(all_plants))

        if args.detail:
            log.info("开始抓详细页：%d 个，预估 %.0f 分钟",
                     len(all_plants), len(all_plants) * fetch.DEFAULT_DELAY / 60)
            for i, p in enumerate(all_plants, 1):
                try:
                    p["detail"] = detail(client, p["detail_url"])
                except Exception as exc:  # noqa: BLE001 —— 单个失败不中断
                    log.warning("详细页失败 %s：%s", p["name"], type(exc).__name__)
                if i % 100 == 0:
                    log.info("  详细页 %d/%d", i, len(all_plants))
    finally:
        client.close()

    dest = out_dir / ("plants_with_detail.json" if args.detail else "plants.json")
    dest.write_text(json.dumps(all_plants, ensure_ascii=False, indent=1), encoding="utf-8")

    size = paths.dir_size(out_dir)
    by_method: dict[str, int] = {}
    for p in all_plants:
        by_method[p.get("method") or "?"] = by_method.get(p.get("method") or "?", 0) + 1
    log.info("完成：%d 个発電所 / %d 个水系 / %s", len(all_plants), systems_count,
             report.human_bytes(size))
    log.info("発電方式分布：%s", by_method)

    report.emit(
        path=str(out_dir),
        bytes=size,
        size_human=report.human_bytes(size),
        rows=len(all_plants),
        river_systems=systems_count,
        with_detail=args.detail,
        by_method=by_method,
        has_coordinates=False,          # ⚠️ 明确记下来，别让人再误以为有
        location_granularity="都道府県 + 市町村（详细页）",
        data_vintage="2014.11.30 入力，之后未更新",
        licence="TO VERIFY —— 電力土木技術協会 利用条件",
        source_url="https://jepoc.or.jp/hydro/",
    )


if __name__ == "__main__":
    main()
