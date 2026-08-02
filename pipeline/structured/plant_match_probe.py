"""発電所コード → 坐标 的匹配率探测（probe，不是正式 asset）。

## 为什么需要这个

`occto_unit` 给的是「発電所コード / 発電所名 / ユニット名 + 30 分出力」，**没有坐标**。
AJG 给的是「発電所几何 + OSM 属性」，**没有出力**。
两者一旦对上，就能做**逐机组出力的地图动画** —— 这是公开世界没有的东西。

对不上的地方才是真问题，所以先探匹配率，再决定用什么策略，不要一上来就写 join。

用法：
    python -m pipeline.structured.plant_match_probe
    python -m pipeline.structured.plant_match_probe --date 20260730
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path

from pipeline.core import fetch, paths

log = logging.getLogger("powerjp.plant_match")

AJG_GLOB = "v*"          # ⚠️ 版を固定しない（CLAUDE.md「上流の版を書かない」）
REGIONS = ["hokkaido", "tohoku", "tokyo", "chubu", "hokuriku",
           "kansai", "chugoku", "shikoku", "kyushu", "okinawa"]

# occto 的 areaCdNm ↔ AJG 的区域目录名
AREA_TO_REGION = {
    "北海道": "hokkaido", "東北": "tohoku", "東京": "tokyo", "中部": "chubu",
    "北陸": "hokuriku", "関西": "kansai", "中国": "chugoku", "四国": "shikoku",
    "九州": "kyushu", "沖縄": "okinawa",
}


# AJG（OSM）会把电源种插进名字里：「苫東厚真**火力**発電所」，
# 而 OCCTO 用「苫東厚真発電所」。这是两边最主要的系统性差异。
FUEL_INFIX = re.compile(r"(火力|水力|原子力|地熱|風力|太陽光|汽力|内燃力|バイオマス)発電所$")

# OCCTO 侧常见的事业者前缀
OPERATOR_PREFIX = re.compile(
    r"^(電源開発|日本製鉄|新日鐵住金|ＪＦＥ|JFE|神戸製鋼所|住友共同電力|"
    r"北海道パワーエンジニアリング|東京電力|中部電力|関西電力|九州電力|"
    r"中国電力|四国電力|東北電力|北陸電力|沖縄電力|北海道電力)"
)

# 末尾的机组/设备标记：「…3G」「…第5号発電設備」「…1号機」「…3号系列」
UNIT_SUFFIX = re.compile(
    r"("
    r"第?\d+号系列第?\d+軸"          # 千葉火力発電所1号系列1軸
    r"|第?\d+号発電設備"
    r"|第?\d+号系列"
    r"|第?\d+軸"
    r"|(GT|ST|CC|ガスタービン)?新?第?\d+号機?"   # 磯子火力発電所新1号機 の「新1号機」
    r"|\d+[GgＧ]"
    r")$"
)

# 「〇〇パワーステーション」を OCCTO 側は「〇〇パワー」と書く。両側とも剥がして揃える。
POWER_SUFFIX = re.compile(r"(パワーステーション|パワー)$")

# 事业者名不一定在开头：「相馬共同火力発電**株式会社**新地火力発電所」。
# 通用做法：出现公司形式词时，取它**之后**的部分。
COMPANY_FORM = re.compile(r"^.*?(株式会社|\(株\)|有限会社|合同会社)")

# ⚠️ 「第二」「第2」是**独立电站**（第二沼沢 ≠ 沼沢、奥清津第二 ≠ 奥清津），
# 绝对不能当噪声剥掉，剥了会错配到另一座电站上。


def normalize(name: str) -> str:
    """把两边的発電所名归一到可比较的形式。

    对不上的常见原因（都是实测出来的，见 §已知坑）：
      1. AJG 在名字里插电源种：苫東厚真**火力**発電所 vs 苫東厚真発電所
      2. OCCTO 带事业者前缀：**電源開発**下郷発電所 vs 下郷水力発電所
      3. OCCTO 名字里带机组标记：…苫小牧共同火力発電所**3G**
      4. 全角/半角、半角片假名、公司形式、括注
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)          # 半角片假名 → 全角、全角数字 → 半角
    s = s.replace(" ", "").replace("　", "")
    s = s.replace("㈱", "(株)")
    s = COMPANY_FORM.sub("", s)                      # 公司形式词之前的都是事业者名
    s = re.sub(r"[（(].*?[）)]", "", s)                # 去括注（如「田子倉発電所(東北)」）
    s = OPERATOR_PREFIX.sub("", s)                   # 没带公司形式词的事业者前缀
    # 机组标记可能叠加（…1号系列1軸 / …新1号機），反复剥到不变为止
    for _ in range(4):
        t = UNIT_SUFFIX.sub("", s)
        if t == s:
            break
        s = t
    s = FUEL_INFIX.sub("発電所", s)                   # 苫東厚真火力発電所 → 苫東厚真発電所
    # 最后连「発電所」本身也剥掉，归一到光秃秃的地名。
    # 沖縄侧 OCCTO 用「金武1号」这种连「発電所」都不带的写法，
    # 不剥就永远对不上 AJG 的「金武火力発電所」。
    for _ in range(4):
        t = re.sub(r"(発電所|発電設備)$", "", s)
        t = POWER_SUFFIX.sub("", t)
        t = UNIT_SUFFIX.sub("", t)                   # 剥完后可能又露出机组号
        if t == s:
            break
        s = t
    return s.strip()


def load_occto_units(day: str | None) -> list[dict]:
    root = paths.raw("occto_unit")
    files = sorted(root.rglob("unit_*.json"))
    if not files:
        raise SystemExit(f"{root} 下没有数据，先跑 occto_unit ingest")
    target = None
    if day:
        hits = [f for f in files if day in f.name]
        target = hits[0] if hits else None
    target = target or files[-1]
    log.info("occto_unit 样本：%s", target.name)
    data = json.loads(target.read_text(encoding="utf-8"))
    return data["data"]["items"]


def load_ajg_plants() -> list[dict]:
    base = paths.latest_version("ajg", AJG_GLOB) / "source"
    roots = [p for p in base.iterdir() if p.is_dir()] if base.exists() else []
    if not roots:
        raise SystemExit(f"{base} 下没有 AJG 源，先跑 ajg ingest")
    data_dir = roots[0] / "data"
    plants: list[dict] = []
    # ⚠️ 除了 {region}_plants.geojson，还有 *_plants_supplement.geojson
    #    （tokyo 有 4,608 个）。早先只 glob 前者，supplement 一直被漏掉。
    files = [data_dir / f"{r}_plants.geojson" for r in REGIONS]
    files += sorted(data_dir.glob("*_plants_supplement.geojson"))
    for f in files:
        region = f.stem.split("_")[0]
        if not f.is_file():
            continue
        gj = json.loads(f.read_text(encoding="utf-8"))
        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            plants.append({
                "region": region,
                "name": props.get("name") or props.get("name:ja") or "",
                "name_ja": props.get("name:ja") or "",
                "source": props.get("plant:source"),
                "output": props.get("plant:output:electricity"),
                "operator": props.get("operator"),
                "geometry": feat.get("geometry"),
            })
    return plants


def main() -> None:
    ap = argparse.ArgumentParser(description="発電所名 匹配率探测")
    ap.add_argument("--date", help="用哪一天的 occto_unit 样本 YYYYMMDD")
    ap.add_argument("--show", type=int, default=15, help="列出多少条未匹配样本")
    args = ap.parse_args()
    fetch.setup_logging()

    items = load_occto_units(args.date)
    plants = load_ajg_plants()

    # occto 侧：按発電所去重
    occto: dict[str, dict] = {}
    for it in items:
        cd = it.get("htdnsCd")
        if cd and cd not in occto:
            occto[cd] = {
                "cd": cd,
                "name": it.get("htdnsNm", ""),
                "area": it.get("areaCdNm", ""),
                "fuel": it.get("htdnNm", ""),
                "norm": normalize(it.get("htdnsNm", "")),
            }

    log.info("occto 発電所 %d 个 / AJG plants %d 个", len(occto), len(plants))

    # AJG 侧索引：全国 + 按区域
    by_norm: dict[str, list[dict]] = {}
    for p in plants:
        for nm in {p["name"], p["name_ja"]}:
            n = normalize(nm)
            if n:
                by_norm.setdefault(n, []).append(p)

    exact = same_area = 0
    unmatched: list[dict] = []
    for o in occto.values():
        cands = by_norm.get(o["norm"])
        if not cands:
            unmatched.append(o)
            continue
        exact += 1
        want = AREA_TO_REGION.get(o["area"])
        if any(c["region"] == want for c in cands):
            same_area += 1

    n = len(occto)
    print()
    print("═" * 62)
    print(f"  occto 発電所           {n:>5}")
    print(f"  名称归一后匹配上       {exact:>5}  ({exact/n*100:.1f}%)")
    print(f"    其中区域也一致       {same_area:>5}  ({same_area/n*100:.1f}%)")
    print(f"  未匹配                 {len(unmatched):>5}  ({len(unmatched)/n*100:.1f}%)")
    print("═" * 62)

    print(f"\n未匹配按区域：{dict(Counter(u['area'] for u in unmatched))}")
    print(f"未匹配按电源：{dict(Counter(u['fuel'] for u in unmatched))}")
    print(f"\n未匹配样本（前 {args.show} 条）：")
    for u in unmatched[:args.show]:
        print(f"  {u['cd']:8} {u['area']:5} {u['fuel']:12} {u['name']}")


if __name__ == "__main__":
    main()
