"""発電所 → 坐标：多源合池 + 事业者消歧 + 打分选点 + xref 兜底。

## 这个脚本回答什么

「OCCTO 的逐机组出力，有多少能**正确地**画到地图上？」
—— 分母不是电站个数，而是**当日発電量**。少一座 0.01% 的自备电厂无所谓，
少一座 1% 的联合循环就是地图上肉眼可见的空洞。

⚠️ 「名字对上了」≠「坐标是对的」。见下面两节，两类静默错误都不掉覆盖率、
不报错，只会让人对着一张看似完整的图做错判断。`plant_verify.py` 专门查这个。

## ★ 静默错误 A：日本地名重名，小设施撞上大电站

AJG 的 19,138 条 plants 绝大多数是小型太阳能。归一化剥掉「発電所」后名字很短
（`広野`/`高浜`/`竹原`），**极易与大型火力撞名**。

早先取候选用的是 `pool[name][0]`，顺序完全由文件遍历决定 —— 等于随机挑一个同名
设施。实测 `広野` 因此取到了**徳島県的広野**，而 OCCTO 的広野是福島県的広野火力，
**差 716 km**，占当日発電量 1.04%。

修法：候选多个时按 `score()` 打分选，见下。

## ★ 静默错误 B：不同公司的同址电站被归一成一座

日本大量电站是**同地名、不同公司、相邻厂址**：

| 归一名 | 实际是两座 |
|---|---|
| 松浦 | 九州電力 松浦発電所 ＋ J-POWER 電源開発松浦火力発電所 |
| 橘湾 | 四国電力 橘湾発電所 ＋ 電源開発橘湾火力発電所（同在阿南市橘湾） |
| 敦賀 | 北陸電力 敦賀火力 ＋ 日本原電 敦賀（原発） |

`normalize()` 无条件剥事业者前缀，于是两座并成一个 key、出力相加、画到一个点上。

**判据很干净**：OCCTO 自己用不同 `htdnsCd` 区分（松浦 `95062` vs `950AB`），
而「一站多码」的情形（奈井江 `10203`/`10212` = 1号機/2号機）**原名完全相同**。
所以：**原名里的事业者部分不同 = 不同电站**。

修法：电站键 = `(事业者, 归一名)`，见 `split_operator()`。

## 坐标池的三个源

| 源 | 实测独立贡献 | 说明 |
|---|---|---|
| AJG（OSM） | 基础 | `{region}_plants*` + `docs/data/generators`（≥10MW 机组层）|
| **GEM** | **+5.0pt** | 唯一有真实增量的补充源 |
| ksj_p03 | **+0pt** | 全被前两者覆盖。保留只为多源互验 |

## ⚠️ 两个把电站从池子里静默弄丢的坑

1. **GEM 的 local 名是「名称, 事业者」形式**。`COMPANY_FORM` 是贪婪前缀剥离，
   `福島いわきバイオマス発電所, エイブルエナジー合同会社` 会被整段吃成空串。
   → **按逗号拆分后每段分别入池**。
2. **GEM 有一批只有英文名、local 名为空**（姫路天然ガス、福島天然ガス、
   大分製鐵、トクヤマ東）。日文侧永远匹配不上，只能走 xref。
   ⚠️ 下结论说「三源皆无」之前，**三个源 × 所有名称字段全搜一遍**（LESSONS #20）。

## xref 的优先级：自动匹配优先，人工只兜底

上游日后收录了某座电站，自动匹配就会命中，xref 条目**不再被查**，
脚本报成「🗑 冗余」提示删除。**人工结果永远挡不住上游改进。**

用法：
    python -m pipeline.structured.plant_coords
    python -m pipeline.structured.plant_coords --date 20260731 --show 20
"""

import argparse
import json
import logging
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import openpyxl
import shapefile

from pipeline.core import fetch, paths, report
from pipeline.structured.plant_match_probe import (COMPANY_FORM, OPERATOR_PREFIX,
                                                   normalize)

log = logging.getLogger("powerjp.plant_coords")

AJG_TAG = "v1.6.0"
GEM_XLSX = "Global-Integrated-Power-March-2026-II.xlsx"
XREF = Path(__file__).with_name("plant_xref.json")

# 区域粗略参考点。⚠️ 只用于**软打分**，绝不做硬拒 ——
# 电力区域≠地理地方（いわき地理属東北、供电属東京），硬拒误杀过 4.1pt（FLOW_MATCHING）
AREA_REF = {
    "北海道": (43.06, 141.35), "東北": (38.27, 140.87), "東京": (35.68, 139.77),
    "中部": (35.18, 136.91), "北陸": (36.70, 137.21), "関西": (34.69, 135.50),
    "中国": (34.39, 132.46), "四国": (33.84, 132.77), "九州": (33.59, 130.40),
    "沖縄": (26.21, 127.68),
}

# 电源种归一到共同集合，供 OCCTO ↔ AJG ↔ GEM 比对
FUEL_OCCTO = {
    "火力（石炭）": "coal", "火力（ガス）": "gas", "火力（石油）": "oil",
    "原子力": "nuclear", "水力": "hydro", "地熱": "geothermal",
    "風力": "wind", "太陽光": "solar",
}
# ⚠️ AJG 有**两套**电源种字段，必须都读：
#   OSM 图层（{region}_plants）→ `plant:source`
#   generators.geojson         → `fuel_type`（lng / pumped_hydro 这些是它独有的写法）
# 早先只读 plant:source，于是 generators 这个**最可信的层**电源种全是空，
# 「敦賀火力 vs 敦賀原子力」这类同地名不同电源就区分不开，实测选错了。
FUEL_AJG = {
    "coal": "coal", "gas": "gas", "lng": "gas", "oil": "oil", "diesel": "oil",
    "nuclear": "nuclear", "hydro": "hydro", "pumped_hydro": "hydro",
    "geothermal": "geothermal", "wind": "wind", "solar": "solar",
    "biomass": "biomass", "waste": "waste",
    # "mixed" 故意不映射 —— 混烧机组给不出确定信号，宁可不加分也不要误扣
}
FUEL_GEM = {
    "coal": "coal", "nuclear": "nuclear", "hydropower": "hydro",
    "geothermal": "geothermal", "wind": "wind", "bioenergy": "biomass",
    "utility-scale solar": "solar",
}
# GEM 把石油和天然气并成一类，对上 OCCTO 的 gas 或 oil 都算数
FUEL_EITHER = {"oil/gas": {"gas", "oil"}}

COMPANY_TAIL = re.compile(r"(株式会社|\(株\)|有限会社|合同会社|㈱)")

# ★ 从**名字**推断电源种。`normalize()` 会把「風力/火力」当噪声剥掉，
#   但对没有电源种字段的源（P03 全部、AJG 部分 plants）那是唯一的信号。
#   实测：`ゼロワットパワー市原発電所`（火力ガス）曾因此选中 P03 的**市原風力発電所**。
#   ⚠️ 顺序有意义 —— 「火力」放最后，否则「バイオマス・石炭混焼火力」会被判成 thermal。
FUEL_IN_NAME = [("風力", "wind"), ("太陽光", "solar"), ("メガソーラー", "solar"),
                ("地熱", "geothermal"), ("原子力", "nuclear"),
                ("バイオマス", "biomass"), ("揚水", "hydro"), ("水力", "hydro"),
                ("火力", "thermal")]
# 名字只写「火力」时分不出煤/气/油，用这个宽类去对 OCCTO 的三种火力
THERMAL = {"coal", "gas", "oil"}


def fuel_from_name(s: str) -> str | None:
    for kw, f in FUEL_IN_NAME:
        if kw in s:
            return f
    return None


def norm_operator(s: str) -> str:
    """事业者名归一。「電源開発株式会社」「電源開発」→ 同一个。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).replace(" ", "").replace("　", "")
    s = COMPANY_TAIL.sub("", s)
    return s.strip()


def split_operator(name: str) -> tuple[str, str]:
    """発電所名 → (事业者, 归一后的电站名)。

    ★ 这是拆开「静默错误 B」的关键：`電源開発松浦火力発電所` → (電源開発, 松浦)，
    `松浦発電所` → ('', 松浦)。事业者不同 ⇒ 不是同一座电站。

    ⚠️ 事业者可能带公司形式词在中间（`相馬共同火力発電株式会社新地火力発電所`），
    也可能是裸前缀（`北陸電力 敦賀火力発電所`），两种都要认。
    """
    s = unicodedata.normalize("NFKC", str(name)).replace(" ", "").replace("　", "")
    op = ""
    m = COMPANY_FORM.match(s)
    if m:
        op, s = m.group(0), s[m.end():]
    else:
        m = OPERATOR_PREFIX.match(s)
        if m:
            op, s = m.group(0), s[m.end():]
    return norm_operator(op), normalize(s)


def haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def valid(pt) -> bool:
    """坐标可用且落在日本范围内。GEM 有少量空/0 坐标。"""
    if not pt or pt[0] is None or pt[1] is None:
        return False
    try:
        lat, lon = float(pt[0]), float(pt[1])
    except (TypeError, ValueError):
        return False
    return 20.0 < lat < 46.5 and 122.0 < lon < 154.0


def _centroid(geom: dict):
    c = (geom or {}).get("coordinates")
    t = (geom or {}).get("type")
    if not c:
        return None
    if t == "Point":
        return (c[1], c[0])
    if t == "Polygon":
        r = c[0]
        return (sum(p[1] for p in r) / len(r), sum(p[0] for p in r) / len(r))
    if t == "MultiPolygon":
        r = c[0][0]
        return (sum(p[1] for p in r) / len(r), sum(p[0] for p in r) / len(r))
    return None


def _add(pool: dict, raw: str, **kw):
    """入池。⚠️ GEM 的 local 名是「名称, 事业者」，必须拆开分别归一。

    不拆的话 COMPANY_FORM 会把逗号前面整段当事业者前缀剥掉，得到空串（LESSONS #21）。
    """
    for part in str(raw).split(","):
        op, n = split_operator(part.strip())
        if not n:
            continue
        rec = {"raw": str(raw), **kw}
        if not rec.get("op"):
            rec["op"] = op
        # 源没给电源种时，退而从名字推断（标记来源，打分时权重减半）
        if not rec.get("fuel"):
            hint = fuel_from_name(part)
            if hint:
                rec["fuel"], rec["fuel_src"] = hint, "name"
        lst = pool.setdefault(n, [])
        if rec not in lst:
            lst.append(rec)


def build_pool() -> dict:
    """归一名 → [候选记录, …]。

    ⚠️ 保留**全部**候选而不是先到先得 —— 选哪个由 `score()` 定，
    并且 `plant_verify.py` 要靠多源坐标互相校验，只留一个就发现不了错配。
    """
    pool: dict[str, list] = {}

    # ---- AJG ----
    base = paths.raw("ajg", AJG_TAG) / "source"
    src = [p for p in base.iterdir() if p.is_dir()][0]
    files = sorted((src / "data").glob("*_plants*.geojson"))
    files += [src / "docs" / "data" / "generators.geojson",
              src / "docs" / "data" / "plants_utility.geojson"]
    for f in files:
        if not Path(f).is_file():
            continue
        layer = Path(f).stem            # generators / plants_utility / {region}_plants
        for ft in json.loads(Path(f).read_text(encoding="utf-8")).get("features", []):
            pr = ft.get("properties", {}) or {}
            pt = _centroid(ft.get("geometry"))
            raw_fuel = pr.get("fuel_type") or pr.get("plant:source") or ""
            fuel = FUEL_AJG.get(str(raw_fuel).split(";")[0].strip().lower())
            op = norm_operator(pr.get("operator") or "")
            cap = pr.get("capacity_mw")
            # ⚠️ OSM 的 name 有时是罗马字，日文在 name:ja —— 三个变体都要收（LESSONS #16）
            for k in ("name:ja", "name", "name:en"):
                if pr.get(k):
                    _add(pool, pr[k], source="AJG", layer=layer, pt=pt,
                         fuel=fuel, op=op, cap=cap)
    log.info("AJG      池内 %d", len(pool))
    n0 = len(pool)

    # ---- 国土数値情報 P03（独立贡献 0pt，留作多源互验）----
    for shp in sorted((paths.raw("ksj_p03") / "P03-13" / "extracted" / "SHP").glob("*.shp")):
        r = shapefile.Reader(str(shp.with_suffix("")), encoding="cp932",
                             encodingErrors="replace")
        fl = [x[0] for x in r.fields[1:]]
        if "P03_0002" not in fl:      # ⚠️ P03_0002=発電所名，P03_0001 是事業者名
            continue
        i, io = fl.index("P03_0002"), (fl.index("P03_0001") if "P03_0001" in fl else None)
        for sr in r.iterShapeRecords():
            pts = sr.shape.points
            _add(pool, str(sr.record[i] or "").strip(), source="P03", layer="p03",
                 pt=(pts[0][1], pts[0][0]) if pts else None, fuel=None, cap=None,
                 op=norm_operator(sr.record[io] if io is not None else ""))
    log.info("+ P03    池内 %d  (+%d)", len(pool), len(pool) - n0)
    n1 = len(pool)

    # ---- GEM ----
    gem = paths.raw("gem") / "2026-08-01" / GEM_XLSX
    wb = openpyxl.load_workbook(gem, read_only=True)
    rows = wb["Power facilities"].iter_rows(values_only=True)
    hdr = next(rows)
    C = {h: i for i, h in enumerate(hdr) if h}
    owner_key = next((k for k in ("Owner", "Operator", "Parent") if k in C), None)
    for row in rows:
        if str(row[C["Country/area"]]).strip() != "Japan":
            continue
        typ = str(row[C["Type"]] or "").strip().lower()
        fuel = FUEL_GEM.get(typ) or FUEL_EITHER.get(typ)
        _kw = {"source": "GEM", "layer": "gem",
               "pt": (row[C["Latitude"]], row[C["Longitude"]]), "fuel": fuel,
               "cap": row[C["Capacity (MW)"]] if "Capacity (MW)" in C else None,
               "op": norm_operator(row[C[owner_key]] if owner_key else "")}
        for key in ("Plant / Project name (local)", "Plant / Project name",
                    "Plant / Project name (other)"):
            if key in C and row[C[key]]:
                _add(pool, row[C[key]], **_kw)
    log.info("+ GEM    池内 %d  (+%d)", len(pool), len(pool) - n1)
    return pool


def score(o: dict, c: dict) -> float:
    """候选打分。分越高越可能是同一座电站。

    ⚠️ 全部是**软**信号 —— 任何一项都不做硬拒。
    区域尤其不能硬拒：电力区域≠地理地方，硬拒在潮流侧误杀过 4.1pt。
    """
    s = 0.0
    # ① 图层：generators 是 ≥10MW 机组层，与 occto_unit 同层级，最可信
    s += {"generators": 40.0, "plants_utility": 20.0}.get(c.get("layer"), 0.0)
    # ② 电源种一致 —— 拆开「広野火力 vs 広野太阳能」这类撞名的主力信号
    cf, of = c.get("fuel"), o.get("fuel")
    if of and cf:
        # 名字推断出来的只写「火力」，分不出煤/气/油，对上三种火力都算数（分低一档）
        if cf == "thermal":
            s += 18.0 if of in THERMAL else -20.0
        else:
            ok = (cf == of) if isinstance(cf, str) else (of in cf)
            w = 18.0 if c.get("fuel_src") == "name" else 35.0
            s += w if ok else -(w * 0.7)   # 不一致扣分但不否决，源的分类也可能错
    # ③ 事业者一致
    if o.get("op") and c.get("op"):
        s += 30.0 if o["op"] == c["op"] else -10.0
    # ④ 离所属区域参考点近（软，随距离线性衰减到 0）
    ref, pt = AREA_REF.get(o.get("area", "")), c.get("pt")
    if ref and valid(pt):
        d = haversine_km(ref, (float(pt[0]), float(pt[1])))
        s += 25.0 * max(0.0, 1.0 - d / 400.0)
    if valid(pt):
        s += 5.0               # 有可用坐标本身就比没有强
    return s


def pick(o: dict, cands: list) -> dict | None:
    """从同名候选里选最可能的那个。"""
    usable = [c for c in cands if valid(c.get("pt"))]
    return max(usable, key=lambda c: score(o, c)) if usable else None


def load_occto(day: str | None) -> tuple[dict, int]:
    """(事业者, 归一名) → {kwh, raw, fuel, area}，以及当日総発電量。

    ★ 键带事业者，是为了拆开 松浦 / 橘湾 / 敦賀 这类「同地名不同公司」（静默错误 B）。
    """
    files = sorted((paths.raw("occto_unit")).rglob("unit_*.json"))
    if not files:
        raise SystemExit("occto_unit 没有数据，先跑 ingest")
    target = next((f for f in files if day and day in f.name), files[-1])
    log.info("occto_unit 样本：%s", target.name)
    items = json.loads(target.read_text(encoding="utf-8"))["data"]["items"]

    occ: dict[tuple, dict] = defaultdict(
        lambda: {"kwh": 0, "raw": set(), "fuel": None, "area": "", "cd": set()})
    for it in items:
        op, n = split_operator(it.get("htdnsNm", ""))
        if not n:
            continue
        kwh = 0
        for k, v in it.items():
            if k.startswith("htdnRslt"):
                try:
                    kwh += int(str(v).replace(",", "") or 0)
                except ValueError:
                    pass
        e = occ[(op, n)]
        e["kwh"] += kwh
        e["raw"].add(it["htdnsNm"])
        e["cd"].add(it.get("htdnsCd"))
        e["fuel"] = FUEL_OCCTO.get(it.get("htdnNm", ""))
        e["area"] = it.get("areaCdNm", "")
        e["op"], e["name"] = op, n
    return occ, sum(v["kwh"] for v in occ.values())


def resolve(occ: dict, pool: dict, xref: dict) -> tuple[dict, list, list]:
    """每座 OCCTO 电站 → 选定的候选。自动匹配优先，xref 只兜底。"""
    xmap = {e["occto"]: e for e in xref["entries"]}
    got: dict[tuple, dict] = {}
    for key, o in occ.items():
        c = pick(o, pool.get(o["name"], []))
        if c:
            got[key] = {**c, "how": "auto"}

    redundant, dangling = [], []
    for name, e in xmap.items():
        hits = [k for k, o in occ.items() if o["name"] == name]
        if not hits:
            dangling.append({**e, "_why": "occto 侧不存在（归一化规则可能变了）"})
            continue
        if all(k in got for k in hits):
            redundant.append(e)          # 上游已能自动匹配 —— 这条可以删
            continue
        tn = split_operator(e["target"].split(",")[0])[1]
        cands = pool.get(tn, [])
        if not cands:
            dangling.append({**e, "_why": f"target 归一为「{tn}」，池子里查不到"})
            continue
        for k in hits:
            if k not in got:
                c = pick(occ[k], cands)
                if c:
                    got[k] = {**c, "how": "xref"}
    return got, redundant, dangling


def main() -> None:
    ap = argparse.ArgumentParser(description="発電所→坐标 覆盖率（按発電量加权）")
    ap.add_argument("--date", help="YYYYMMDD")
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--dump", nargs="?", const="", help="落盘到 storage/structured/plants/")
    args = ap.parse_args()
    fetch.setup_logging()

    pool = build_pool()
    occ, tot = load_occto(args.date)
    xref = json.loads(XREF.read_text(encoding="utf-8"))
    got, redundant, dangling = resolve(occ, pool, xref)

    n_auto = sum(1 for v in got.values() if v["how"] == "auto")
    kwh_auto = sum(occ[k]["kwh"] for k, v in got.items() if v["how"] == "auto")
    kwh_all = sum(occ[k]["kwh"] for k in got)
    miss = sorted(((occ[k]["kwh"], k) for k in occ if k not in got), reverse=True)

    print()
    print("═" * 68)
    print(f"  当日総発電量            {tot:>16,} kWh")
    print(f"  OCCTO 电站（事业者消歧后） {len(occ):>5} 座")
    print(f"  自动匹配                {n_auto:>5} 座   {kwh_auto/tot*100:6.2f}%")
    print(f"  + xref 兜底             {len(got)-n_auto:>5} 座   "
          f"{(kwh_all-kwh_auto)/tot*100:6.2f}%")
    print(f"  ★ 合计覆盖              {len(got):>5} 座   {kwh_all/tot*100:6.2f}%")
    print(f"  未匹配                  {len(miss):>5} 座   "
          f"{sum(m[0] for m in miss)/tot*100:6.2f}%")
    print("═" * 68)

    if redundant:
        print(f"\n🗑  xref 冗余 {len(redundant)} 条 —— 上游已能自动匹配，可以删：")
        for e in redundant:
            print(f"    {e['occto']}  →  {e['target']}")
    if dangling:
        print(f"\n⚠️  xref 失效 {len(dangling)} 条 —— 写了但没生效：")
        for e in dangling:
            print(f"    {e['occto']}  →  {e['target']}\n        {e['_why']}")
    if not redundant and not dangling:
        print("\n✅ xref 全部生效，无冗余无失效")

    if args.dump is not None:
        out = Path(args.dump) if args.dump else paths.structured("plants") / "plants.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        recs = []
        for k, o in occ.items():
            c = got.get(k)
            recs.append({
                "operator": k[0], "name": k[1], "area": o["area"], "fuel": o["fuel"],
                "htdns_cd": sorted(x for x in o["cd"] if x),
                "raw_names": sorted(o["raw"]), "kwh_sample_day": o["kwh"],
                "lat": float(c["pt"][0]) if c and valid(c.get("pt")) else None,
                "lon": float(c["pt"][1]) if c and valid(c.get("pt")) else None,
                # ★ 前端/下游必须能区分置信度
                "coord_source": c["source"] if c else None,
                "coord_layer": c.get("layer") if c else None,
                "match_method": c["how"] if c else None,      # auto / xref
                "matched_name": c["raw"] if c else None,
            })
        out.write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
        log.info("写出 %d 座 → %s", len(recs), out)

    report.emit(
        plants_total=len(occ), matched=len(got), matched_auto=n_auto,
        matched_xref=len(got) - n_auto, unmatched=len(miss),
        coverage_pct_by_generation=round(kwh_all / tot * 100, 4),
        xref_redundant=len(redundant), xref_dangling=len(dangling),
        sample_day_gwh=round(tot / 1e6, 1),
        output=str(paths.structured("plants") / "plants.json"),
    )

    print(f"\n未匹配明细（前 {args.show}，按発電量降序）：")
    for kwh, k in miss[:args.show]:
        v = occ[k]
        cands = pool.get(v["name"], [])
        print(f"  {kwh:>13,} kWh ({kwh/tot*100:5.2f}%) {v['area']:4} "
              f"op={v['op'] or '—':10} {sorted(v['raw'])[0][:38]}")
        if cands:
            print(f"        同名候选 {len(cands)} 个但都无有效坐标或被排除："
                  f"{[(c['raw'][:20], c['source']) for c in cands[:3]]}")


if __name__ == "__main__":
    main()
