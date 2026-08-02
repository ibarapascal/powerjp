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

# ⚠️ バージョンやダウンロード日を**書かない**。固定すると新しいデータを
#    取り込んでも古い方を読み続け、しかもエラーが出ない（LESSONS #34）。
#    `paths.latest_version()` / `latest_file()` が常に最新を指す。
AJG_GLOB = "v*"
GEM_GLOB = "Global-Integrated-Power-*.xlsx"
XREF = Path(__file__).with_name("plant_xref.json")
# 設備容量の人手補完（①〜③ の自動取得が全滅したものだけ）
CAP_XREF = Path(__file__).with_name("capacity_xref.json")

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
    # ⚠️ 「その他」と空欄を落とすと 12 か所（鹿島 7,305MW を含む）が
    #    電源種なしになり、地図で色が付かない。実体はほぼ製鉄所などの
    #    自家発（高炉ガス・コークス炉ガス等の副生ガス）で、火力の一種。
    #    ただし内訳は公表されないので独立した区分として持つ。
    "その他": "other_thermal",
    "": "other_thermal",
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
    base = paths.latest_version("ajg", AJG_GLOB) / "source"
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
            # ★ `capacity_mw_sourced` は**発電所全体**の容量で、出典 URL と
            #    根拠メモ（capacity_source_note）付き。同一発電所の全行が同じ値。
            #    一方 `capacity_mw` は**行ごとの推定値**で、同一発電所でも
            #    1500/1920/1920/1920 のようにばらつく。これを合算すると
            #    川崎火力が 7,260MW（実際 3,420MW）になる。
            #    → sourced があればそれを使い、**合算しない**。
            cap = pr.get("capacity_mw_sourced")
            cap_plant_level = cap is not None
            if cap is None:
                cap = pr.get("capacity_mw")
            # ⚠️ OSM 的 name 有时是罗马字，日文在 name:ja —— 三个变体都要收（LESSONS #16）
            for k in ("name:ja", "name", "name:en"):
                if pr.get(k):
                    _add(pool, pr[k], source="AJG", layer=layer, pt=pt,
                         fuel=fuel, op=op, cap=cap, cap_plant=cap_plant_level,
                         # ⚠️ generators.geojson は同一ユニットが複数行に現れる
                         #    （西名古屋火力は 4 行）。容量を合算するとき二重計上
                         #    しないよう、行ではなく**ユニット ID** で数える。
                         uid=pr.get("id"))
    log.info("AJG      池内 %d", len(pool))
    n0 = len(pool)

    # ---- 国土数値情報 P03（独立贡献 0pt，留作多源互验）----
    p03 = paths.latest_version("ksj_p03", "P03-*") / "extracted" / "SHP"
    for shp in sorted(p03.glob("*.shp")):
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
                 uid=None, cap_plant=False,
                 op=norm_operator(sr.record[io] if io is not None else ""))
    log.info("+ P03    池内 %d  (+%d)", len(pool), len(pool) - n0)
    n1 = len(pool)

    # ---- GEM ----
    wb = openpyxl.load_workbook(paths.latest_file("gem", GEM_GLOB), read_only=True)
    rows = wb["Power facilities"].iter_rows(values_only=True)
    hdr = next(rows)
    C = {h: i for i, h in enumerate(hdr) if h}
    owner_key = next((k for k in ("Owner", "Operator", "Parent") if k in C), None)
    uid_key = next((k for k in ("GEM unit/phase ID", "GEM location ID") if k in C), None)
    for row in rows:
        if str(row[C["Country/area"]]).strip() != "Japan":
            continue
        typ = str(row[C["Type"]] or "").strip().lower()
        fuel = FUEL_GEM.get(typ) or FUEL_EITHER.get(typ)
        _kw = {"source": "GEM", "layer": "gem",
               "pt": (row[C["Latitude"]], row[C["Longitude"]]), "fuel": fuel,
               "cap": row[C["Capacity (MW)"]] if "Capacity (MW)" in C else None,
               "op": norm_operator(row[C[owner_key]] if owner_key else ""),
               "uid": (row[C[uid_key]] if uid_key else None),
               # GEM の Capacity は**ユニット単位**なので合算が必要
               "cap_plant": False}
        for key in ("Plant / Project name (local)", "Plant / Project name",
                    "Plant / Project name (other)"):
            if key in C and row[C[key]]:
                _add(pool, row[C[key]], **_kw)
    log.info("+ GEM    池内 %d  (+%d)", len(pool), len(pool) - n1)
    return pool


def load_gem_plants() -> list[dict]:
    """GEM を**発電所単位**で読む（operating のみ、ユニット容量を合算）。

    ★ 名前ではなく**座標**で引くための索引。

    座標の照合は既に 99.94% できている。一方 GEM は原子力などを英語名でしか
    持たず（`Takahama`／local 名が空）、日本語名では永久に引けない
    —— 坐标を補ったときと同じ壁（`PLANT_MATCHING.md` の坑 2）。
    照合済みの座標から引けば、名前の表記ゆれを一切迂回できる。
    """
    wb = openpyxl.load_workbook(paths.latest_file("gem", GEM_GLOB), read_only=True)
    rows = wb["Power facilities"].iter_rows(values_only=True)
    hdr = next(rows)
    C = {h: i for i, h in enumerate(hdr) if h}
    agg: dict = {}
    for r in rows:
        if str(r[C["Country/area"]]).strip() != "Japan":
            continue
        if str(r[C["Status"]] or "").strip().lower() != "operating":
            continue
        pt = (r[C["Latitude"]], r[C["Longitude"]])
        if not valid(pt):
            continue
        try:
            mw = float(r[C["Capacity (MW)"]] or 0)
        except (TypeError, ValueError):
            continue
        if mw <= 0:
            continue
        name = str(r[C["Plant / Project name"]] or "").strip()
        typ = str(r[C["Type"]] or "").strip().lower()
        key = (name, typ)
        e = agg.setdefault(key, {"name": name, "type": typ, "mw": 0.0,
                                 "lat": 0.0, "lon": 0.0, "n": 0,
                                 "fuel": FUEL_GEM.get(typ) or FUEL_EITHER.get(typ),
                                 "local": str(r[C["Plant / Project name (local)"]] or "")})
        e["mw"] += mw
        e["lat"] += float(pt[0]); e["lon"] += float(pt[1]); e["n"] += 1
    out = []
    for e in agg.values():
        e["lat"] /= e["n"]; e["lon"] /= e["n"]
        out.append(e)
    log.info("GEM 発電所単位 %d 件（operating・容量つき）", len(out))
    return out


def capacity_by_location(gem_plants: list[dict], pt, o_fuel: str | None,
                         min_mw: float = 0.0,
                         max_km: float = 2.5) -> tuple[float | None, str | None]:
    """照合済みの座標から GEM の発電所を引いて容量を得る。

    ⚠️ 近傍に別の発電所があり得るので、電源種が矛盾するものは外し、
    残った中で**最も近い 1 件**だけを採る（合算しない）。

    ## ★ 物理制約：容量は実出力を下回れない

    同じ敷地に別会社の発電所が並ぶことがある（橘湾＝四国電力 70万kW ＋
    J-POWER 210万kW、松浦＝九電 ＋ J-POWER）。`split_operator` で発電量は
    正しく分離できているのに、容量だけ小さい方を掴むと
    「平均出力 1,883MW＞容量 700MW」という不可能な組み合わせになる。

    `min_mw`（当日平均出力）を渡すと、それを下回る候補を後回しにする。
    ⚠️ 完全に捨てはしない —— 全部下回るなら「小さいけれど唯一の候補」を返す。
    """
    if not valid(pt):
        return None, None
    lat, lon = float(pt[0]), float(pt[1])
    cands = []
    for g in gem_plants:
        d = haversine_km((lat, lon), (g["lat"], g["lon"]))
        if d > max_km:
            continue
        gf = g.get("fuel")
        if o_fuel and gf:
            ok = (gf == o_fuel) if isinstance(gf, str) else (o_fuel in gf)
            if not ok:
                continue
        cands.append((d, g))
    if not cands:
        return None, None
    # 出力を賄える候補を優先し、その中で最も近いもの。無ければ全体で最も近いもの
    fits = [c for c in cands if c[1]["mw"] >= min_mw]
    d, g = min(fits or cands, key=lambda c: c[0])
    tag = "GEM_by_location" if fits or min_mw <= 0 else "GEM_by_location_undersized"
    return round(g["mw"], 1), f"{tag}({d:.1f}km)"


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
        lambda: {"kwh": 0, "raw": set(), "fuel": None, "area": "", "cd": set(),
                 # 当日の 30 分コマごとの出力合計（MW）。容量の下限判定に使う
                 "slots": [0.0] * 48})
    for it in items:
        op, n = split_operator(it.get("htdnsNm", ""))
        if not n:
            continue
        kwh = 0
        slots = []
        for k in sorted(it):
            if not k.startswith("htdnRslt"):
                continue
            try:
                v = int(str(it[k]).replace(",", "") or 0)
            except ValueError:
                v = 0
            kwh += v
            slots.append(v)
        e = occ[(op, n)]
        e["kwh"] += kwh
        for i, v in enumerate(slots[:48]):
            # kWh/30分 → MW（1コマ 0.5h なので ×2、kW→MW で ÷1000）
            e["slots"][i] += v * 2 / 1000.0
        e["raw"].add(it["htdnsNm"])
        e["cd"].add(it.get("htdnsCd"))
        e["fuel"] = FUEL_OCCTO.get((it.get("htdnNm") or "").strip())
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


def plant_capacity(pool: dict, name: str, chosen: dict | None,
                   o_fuel: str | None = None,
                   max_km: float = 10.0) -> tuple[float | None, str | None]:
    """発電所の設備容量（MW）と、その出所。

    ## ⚠️ 源によって「容量」の意味が違う

    | 源 | 粒度 | 現役だけか | 実測での正確さ |
    |---|---|---|---|
    | **GEM** `Capacity (MW)` | ユニット（合算する） | ✅ `Status=operating` で絞れる | 袖ケ浦・姫路第二・川崎は**公表値と完全一致** |
    | AJG `capacity_mw_sourced` | 発電所全体（合算しない） | ❌ **廃炉分を含む** | 大飯 4,710（現役 2,360）、姫路第二 4,119（現役 2,919） |
    | AJG `capacity_mw` | 行ごとの推定 | — | ⚠️ 使わない。同一発電所で 1500/1920/1920/1920 とばらつく |

    → **GEM を優先**し、無いときだけ AJG に落とす。どちらを使ったかを返す。

    ⚠️ AJG 由来の値は「これまでに設置された最大」であり、
    いま動かせる容量ではない。前端でそのまま利用率の分母にしない。

    ## 距離チェックについて

    GEM は正規化名だけで引くので、同名の別発電所を掴む可能性がある
    （日本の地名重複は多い —— `PLANT_MATCHING.md` の静默エラー A）。
    選ばれた座標から離れすぎているものは除く。
    ⚠️ これは「容量を按分するための半径」ではなく「同一施設かの確認」。

    ## ⚠️ 電源種でも絞る

    `normalize()` の `FUEL_INFIX` は「〜バイオマス発電所」の「バイオマス」を
    電源種の挿入とみなして剥がす（`苫東厚真火力発電所` ↔ `苫東厚真発電所` を
    合わせるための規則）。その副作用で **`袖ケ浦バイオマス発電所` が
    `袖ケ浦` に正規化され、袖ケ浦火力の容量に 75MW 混入していた**。
    OCCTO 側の電源種が分かっているなら、それと矛盾する候補は外す。
    """
    if not chosen:
        return None, None
    cpt = chosen.get("pt")

    def ok_fuel(c) -> bool:
        """OCCTO 側の電源種と明確に矛盾する候補を外す。不明なものは残す。"""
        if not o_fuel:
            return True
        cf = c.get("fuel")
        if not cf or cf == "thermal":
            return True
        if isinstance(cf, str):
            return cf == o_fuel
        return o_fuel in cf

    def near(c) -> bool:
        if not (valid(c.get("pt")) and valid(cpt)):
            return True                     # 判定できないものは落とさない
        return haversine_km((float(c["pt"][0]), float(c["pt"][1])),
                            (float(cpt[0]), float(cpt[1]))) <= max_km

    # ① GEM（operating のみ。ユニット単位なので ID で重複排除して合算）
    gem = [c for c in pool.get(name, [])
           if c.get("source") == "GEM" and c.get("cap") and near(c) and ok_fuel(c)]
    if gem:
        seen: set = set()
        total = 0.0
        for c in gem:
            key = c.get("uid") or (c.get("raw"), c["cap"], str(c.get("pt")))
            if key in seen:
                continue
            seen.add(key)
            try:
                total += float(c["cap"])
            except (TypeError, ValueError):
                pass
        if total > 0:
            return round(total, 1), "GEM_operating"

    # ② AJG の発電所全体値（廃炉分を含む点に注意）
    ajg = [c for c in pool.get(name, [])
           if c.get("source") == "AJG" and c.get("cap_plant") and c.get("cap")
           and c.get("op") == chosen.get("op") and near(c) and ok_fuel(c)]
    if ajg:
        return round(max(float(c["cap"]) for c in ajg), 1), "AJG_installed_ever"

    # ③ AJG の行ごとの推定値をユニットとみなして合算する。
    #    ⚠️ **精度は最も低い**。`capacity_mw` は同一発電所でもばらつく
    #    （川崎火力は 1500/1920/1920/1920 で合計 7,260MW ＝ 実際の 2 倍以上）。
    #    `capacity_mw_sourced` があるならそちらが正しいので、ここには来ない。
    #    それも無い発電所で「値が無い」よりはマシ、という位置づけ。
    unit = [c for c in pool.get(name, [])
            if c.get("source") == "AJG" and c.get("cap") and not c.get("cap_plant")
            and c.get("op") == chosen.get("op") and near(c) and ok_fuel(c)]
    if unit:
        seen: set = set()
        total = 0.0
        for c in unit:
            key = c.get("uid") or (c.get("raw"), c["cap"], str(c.get("pt")))
            if key in seen:
                continue
            seen.add(key)
            try:
                total += float(c["cap"])
            except (TypeError, ValueError):
                pass
        if total > 0:
            return round(total, 1), "AJG_unit_estimate"

    return None, None


def resolve_capacity(pool: dict, gem_plants: list[dict], name: str,
                     chosen: dict | None, o_fuel: str | None,
                     xref: dict, min_mw: float = 0.0) -> tuple[float | None, str | None]:
    """容量を決める。**自動優先・人手は最後**（座標の xref と同じ方針）。

    ① 名前で引いた GEM（operating・ユニット合算）
    ② AJG の発電所全体値（`capacity_mw_sourced`。⚠️ 廃炉分を含む）
    ③ **照合済みの座標から引いた GEM** ← 名前が英語しか無い原発などを救う
    ④ `capacity_xref.json`（人手。①〜③ が全滅したものだけ）
    """
    cap, src = plant_capacity(pool, name, chosen, o_fuel)
    # ⚠️ 容量が実出力を下回るのは物理的にあり得ない。同一敷地の別会社を
    #    掴んでいる可能性が高いので、座標引きで取り直す（橘湾・松浦で発生）。
    if cap and min_mw > 0 and cap < min_mw and chosen:
        alt, asrc = capacity_by_location(gem_plants, chosen.get("pt"), o_fuel, min_mw)
        if alt and alt >= min_mw:
            return alt, asrc
        # どの候補も観測出力に届かない ＝ 源のデータが古いか不完全。
        # 物理的にあり得ない値をそのまま出さず、観測値まで引き上げて印を付ける。
        return round(min_mw, 1), f"observed_minimum(src={src})"
    if cap:
        return cap, src
    if chosen:
        cap, src = capacity_by_location(gem_plants, chosen.get("pt"), o_fuel, min_mw)
        if cap:
            return cap, src
    e = xref.get(name)
    if e and e.get("capacity_mw"):
        return float(e["capacity_mw"]), f"xref({e.get('source', 'manual')})"

    # ⑤ 何も無い／得た値が観測出力に満たない場合の最終手段。
    #    ⚠️ これは**下限であって定格ではない**。「その日に実際にこれだけ出た」
    #    という事実だけが根拠。source に observed_minimum と明記し、
    #    前端でも推定であることが分かるようにする。
    if min_mw > 0:
        return round(min_mw, 1), "observed_minimum"
    return None, None


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
    gem_plants = load_gem_plants()
    cap_xref = {}
    if CAP_XREF.is_file():
        cap_xref = {e["name"]: e
                    for e in json.loads(CAP_XREF.read_text(encoding="utf-8"))["entries"]}

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
            # ★ OCCTO が「その他」「空欄」としている電源種を、照合先の情報で補う。
            #   OCCTO の分類が粗いだけで、実体は特定できる：
            #   室蘭中央・釧路・広畑・海田・大分第2 → 石炭
            #   鹿島・四日市霞・相生・麻里布第2 → 石油
            #   福島いわき・田原 → バイオマス
            #   ⚠️ 推定なので `fuel_source` に出所を残す。
            #      同一敷地に別会社がある場合（日本製鉄 鹿島 と JERA 鹿島火力）は
            #      照合先の電源種が実体とずれることがある。
            _fuel, _fsrc = o.get("fuel"), "occto"
            if _fuel in (None, "other_thermal") and c:
                cf = c.get("fuel")
                if isinstance(cf, str) and cf not in ("thermal",):
                    _fuel, _fsrc = cf, "matched"
            # 当日平均出力（MW）＝ 発電量 ÷ 24h。容量の妥当性チェックに使う
            # ⚠️ 平均ではなく**当日の最大 30 分出力**を下限に使う。
            #    平均を使うと、ピークだけ高い発電所の容量を過小に見積もる。
            _avg = max(o.get("slots") or [0.0])
            _cap = resolve_capacity(pool, gem_plants, k[1], c, o.get("fuel"),
                                    cap_xref, _avg)
            recs.append({
                "operator": k[0], "name": k[1], "area": o["area"],
                "fuel": _fuel, "fuel_source": _fsrc,
                "htdns_cd": sorted(x for x in o["cd"] if x),
                # ★ 30 分ごとの出力（MW）48 コマ。扇形は「その時刻の出力 ÷ 容量」で
                #   描くもので、当日累計から出す平均利用率ではない。
                #   24 時間の遡り表示（TODO R1）もこの列をそのまま使う。
                "slots_mw": [round(v, 2) for v in o.get("slots") or []],
                "raw_names": sorted(o["raw"]), "kwh_sample_day": o["kwh"],
                "lat": float(c["pt"][0]) if c and valid(c.get("pt")) else None,
                "lon": float(c["pt"][1]) if c and valid(c.get("pt")) else None,
                # ★ 前端/下游必须能区分置信度
                # ★ 設備容量（MW）。同一発電所のユニットを合算したもの
                "capacity_mw": _cap[0],
                # ⚠️ AJG_installed_ever は廃炉分を含む「これまでの最大」
                "capacity_source": _cap[1],
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

    # ★ capacity_xref の冗長／失効を報告する（座標の xref と同じ仕組み）
    if cap_xref:
        used = {str(r.get("capacity_source") or "") for r in recs} if args.dump is not None else set()
        cap_auto, cap_dangling = [], []
        for nm, e in cap_xref.items():
            hits = [k for k in occ if k[1] == nm]
            if not hits:
                cap_dangling.append((nm, "occto 側に存在しない"))
                continue
            for k in hits:
                a, _s = plant_capacity(pool, k[1], got.get(k), occ[k].get("fuel"))
                if not a and got.get(k):
                    a, _s = capacity_by_location(gem_plants, got[k].get("pt"), occ[k].get("fuel"))
                if a:
                    cap_auto.append((nm, a, e.get("capacity_mw")))
        if cap_auto:
            print(f"\n🗑  capacity_xref 冗長 {len(cap_auto)} 件 —— 自動で取れるようになった：")
            for nm, a, m in cap_auto:
                print(f"    {nm}  自動 {a}MW / 手入力 {m}MW")
        if cap_dangling:
            print(f"\n⚠️  capacity_xref 失効 {len(cap_dangling)} 件：")
            for nm, why in cap_dangling:
                print(f"    {nm} —— {why}")
        if not cap_auto and not cap_dangling:
            print(f"\n✅ capacity_xref {len(cap_xref)} 件すべて有効（自動では取れないものだけ）")

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
