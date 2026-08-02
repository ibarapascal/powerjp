"""潮流 → GIS 绑定的匹配率探测（probe，不是正式 asset）。

## 为什么需要这个

`occto_flow` 给的是「送電線名 + 潮流方向(起点 → 终点) + 30 分 × 48 断面」，**没有坐标**。
AJG 给的是「送電線几何 + 変電所几何」，且**两者 100% 都有名字**。

所以潮流上图有两条路，都要测：

  路 A  送電線名 ↔ AJG 线路名        → 直接拿到真实走线几何（最好）
  路 B  起点站名 / 终点站名 ↔ AJG 変电所 → 两点连直线示意（兜底，但一定画得出来）

路 B 是保险：即使 AJG 缺那条线的几何，只要两端站在，就能画一条示意线。

用法：
    python -m pipeline.structured.flow_match_probe
    python -m pipeline.structured.flow_match_probe --area 3
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path

from pipeline.core import fetch, paths

log = logging.getLogger("powerjp.flow_match")

AJG_TAG = "v1.6.0"

# AJG 给变电所加了设施类型后缀，潮流侧是光名字：新信濃 vs 新信濃変電所
# 潮流侧还会用缩写：北九州ss、讃岐SS、中央ss —— SS = substation
FACILITY_SUFFIX = re.compile(
    r"(変電所|開閉所|送電所|開閉塔|switching\s*station|substation"
    r"|変換所|変換設備|converter\s*station"
    r"|発電所|s/s|sws|cs|c/s|s\.?s\.?|ss)$", re.I   # SWS=switching station, CS=変換所；
    # 「発電所」也要剥：沖縄的「金武火力**変電所**」对应 AJG 的「金武火力**発電所**」
)

# AJG 线路名会带回线号或多线合并：安曇幹線2号線、新岡部線;下児玉線
LINE_ORDINAL = re.compile(r"\d+号線$")

# AJG 有 3,270/11,409 条线是「A変電所~B変電所線」这种端点式命名。
# 潮流侧给的正是两端站名 → 可以用「端点对」反查线路几何（路 C）。
ENDPOINT_LINE = re.compile(r"^(.+?)[~～](.+?)線?$")


# 拓扑图（built/all.json）的节点名带电压后缀：「東通村変電所 154kV」
KV_SUFFIX = re.compile(r"\s*\d+(\.\d+)?\s*kv$", re.I)


def norm_station(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)
    s = KV_SUFFIX.sub("", s)
    s = s.replace(" ", "").replace("　", "")
    s = re.sub(r"[（(].*?[）)]", "", s)          # 去罗马字括注：西大寺変電所 (Saidaiji…)
    s = re.sub(r"^(東京電力|東電|中部電力|関西電力|東北電力|九州電力|"
               r"中国電力|四国電力|北陸電力|北海道電力|沖縄電力)", "", s)
    s = FACILITY_SUFFIX.sub("", s)
    return s.strip()


# AJG 用「/」把同塔并架的多回线写在一个 name 里：「那珂線 / 阿武隈線」
LINE_SPLIT = re.compile(r"[;；/／]")

# 【増強後廃線】（山線）这类括注，以及回线标记 1L / 2L
BRACKETS = re.compile(r"[（(【\[].*?[）)】\]]")
CIRCUIT_MARK = re.compile(r"\d+[LlＬ]$")


def norm_line(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).replace(" ", "").replace("　", "")
    s = BRACKETS.sub("", s)
    s = CIRCUIT_MARK.sub("", s)
    s = LINE_ORDINAL.sub("線", s)               # 安曇幹線2号線 → 安曇幹線
    return s.strip()


def line_variants(name: str) -> set[str]:
    """一个归一名派生出的可匹配形态。

    OCCTO 写「阿武隈**幹線**」而 AJG 写「阿武隈**線**」—— 幹線/線 的写法不统一。
    把两种都放进索引，让匹配时能互相撞上。
    ⚠️ 这条比纯字面匹配松，配套要用端点做交叉验证（见 `--strict`）。
    """
    n = norm_line(name)
    if not n:
        return set()
    out = {n}
    if n.endswith("幹線"):
        out.add(n[:-2] + "線")
    elif n.endswith("線"):
        out.add(n[:-1] + "幹線")
    return out


def load_ajg():
    base = paths.raw("ajg", AJG_TAG) / "source"
    roots = [p for p in base.iterdir() if p.is_dir()] if base.exists() else []
    if not roots:
        raise SystemExit("先跑 ajg ingest")
    data = roots[0] / "data"
    docs = roots[0] / "docs" / "data"

    subs: dict[str, list[str]] = {}
    for f in data.glob("*_substations.geojson"):
        for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
            p = ft.get("properties", {}) or {}
            for k in ("name", "name:ja"):
                if p.get(k):
                    subs.setdefault(norm_station(p[k]), []).append(p[k])

    lines: dict[str, list[str]] = {}
    pairs: dict[frozenset, list[str]] = {}       # 路 C：端点对 → 线
    for f in data.glob("*_lines.geojson"):
        for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
            p = ft.get("properties", {}) or {}
            for k in ("name", "name:ja"):
                if not p.get(k):
                    continue
                # 「新岡部線;下児玉線」「那珂線 / 阿武隈線」都是多回线合写，拆开各自入索引
                for part in LINE_SPLIT.split(str(p[k])):
                    for n in line_variants(part):
                        lines.setdefault(n, []).append(p[k])
                    # AJG 有 3,270 条线是「A変電所~B変電所線」端点式命名，
                    # 潮流侧给的正是两端站名 → 用端点对反查真实几何
                    m = ENDPOINT_LINE.match(part.strip())
                    if m:
                        a, b = norm_station(m.group(1)), norm_station(m.group(2))
                        if a and b:
                            pairs.setdefault(frozenset((a, b)), []).append(p[k])

    # 端点池 = 変電所 ∪ 発電所（潮流端点偶尔是发电厂，如 喜撰山発電所・敦賀火力発電所）
    for f in list(data.glob("*_plants.geojson")) + [docs / "generators.geojson"]:
        if not f.is_file():
            continue
        for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
            p = ft.get("properties", {}) or {}
            for k in ("name", "name:ja"):
                if p.get(k):
                    n = norm_station(str(p[k]))
                    if n:
                        subs.setdefault(n, []).append(str(p[k]))
    return subs, lines, pairs


def load_flow_rows(area: int | None) -> list[dict]:
    root = paths.raw("occto_flow")
    files = sorted(root.rglob("chinai_kikan_jisseki_*.csv"))
    if not files:
        raise SystemExit("先跑 occto_flow ingest")
    if area:
        files = [f for f in files if f.stem.endswith(f"_{area:02d}")]
    # 每个区域取最新一天即可 —— 线路清单不随日期变
    latest: dict[str, Path] = {}
    for f in files:
        latest[f.stem[-2:]] = f
    rows = []
    for code, f in sorted(latest.items()):
        text = f.read_bytes().decode("shift_jis", errors="replace")
        for r in csv.DictReader(io.StringIO(text)):
            rows.append(r)
    log.info("潮流样本：%d 个区域 / %d 行", len(latest), len(rows))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="潮流 → GIS 匹配率探测")
    ap.add_argument("--area", type=int)
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()
    fetch.setup_logging()

    subs, lines, pairs = load_ajg()
    rows = load_flow_rows(args.area)
    log.info("AJG：端点(変電所+発電所) %d / 線路名 %d / 端点对 %d",
             len(subs), len(lines), len(pairs))

    line_hit = 0
    pair_hit = 0
    real_geom = 0        # A 或 C —— 能拿到真实走线几何
    either = 0
    both_ends = 0
    one_end = 0
    no_end = 0
    miss_lines: list[str] = []
    miss_stations: Counter = Counter()

    seen_lines = set()
    for r in rows:
        ln = (r.get("送電線名") or "").strip()
        direction = (r.get("潮流方向(正方向)") or "").strip()
        area_nm = (r.get("対象エリア") or "").strip()

        key = (area_nm, ln)
        if key in seen_lines:
            continue
        seen_lines.add(key)

        hit_a = bool(line_variants(ln) & lines.keys())
        if hit_a:
            line_hit += 1
        else:
            miss_lines.append(f"{area_nm} {ln}")

        parts = re.split(r"[→⇒>]", direction)
        ends = [norm_station(p) for p in parts if p.strip()]
        found = [e for e in ends if e in subs]
        hit_b = len(ends) >= 2 and len(found) == 2
        # 路 C：用端点对去撞 AJG 的「A~B線」命名，拿到真实几何
        hit_c = len(ends) >= 2 and frozenset(ends[:2]) in pairs
        if hit_c:
            pair_hit += 1
        if hit_a or hit_c:
            real_geom += 1
        if hit_a or hit_b or hit_c:
            either += 1
        if hit_b:
            both_ends += 1
        elif found:
            one_end += 1
        else:
            no_end += 1
            for e, raw in zip(ends, parts):
                if e not in subs:
                    miss_stations[raw.strip()] += 1

    n = len(seen_lines)
    print()
    print("═" * 66)
    print(f"  潮流的「区域 × 送電線」条目      {n:>5}")
    print(f"  路 A  送電線名 命中 AJG 线路     {line_hit:>5}  ({line_hit/n*100:.1f}%)")
    print(f"  路 C  端点对 命中「A~B線」       {pair_hit:>5}  ({pair_hit/n*100:.1f}%)")
    print(f"  路 B  两端变电所都命中           {both_ends:>5}  ({both_ends/n*100:.1f}%)")
    print(f"        只命中一端                 {one_end:>5}  ({one_end/n*100:.1f}%)")
    print(f"        两端都没命中               {no_end:>5}  ({no_end/n*100:.1f}%)")
    print("  " + "─" * 62)
    print(f"  ★ 真实几何（A∪C）               {real_geom:>5}  ({real_geom/n*100:.1f}%)")
    print(f"  ★ 至少能上图（A∪B∪C）           {either:>5}  ({either/n*100:.1f}%)")
    print("═" * 66)

    print(f"\n未命中的送電線名（前 {args.show}）：")
    for m in miss_lines[:args.show]:
        print("  ", m)
    print(f"\n未命中的站名 top {args.show}：")
    for s, c in miss_stations.most_common(args.show):
        print(f"   {c:>3}×  {s}")


if __name__ == "__main__":
    main()
