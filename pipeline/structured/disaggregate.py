"""非调度电源：把区域级实绩摊分到逐站（水力／風力／地熱／バイオマス）。

## 两层结构 —— 和调度机组严格分开

| 层 | 数据 | 性质 | 脚本 |
|---|---|---|---|
| **调度机组** | `occto_unit` 逐机组 30 分 | **实测** | `plant_coords.py` |
| **非调度电源** | 区域实绩 ÷ 站装机容量 | **推算** | 本脚本 |

输出每条都带 `data_quality`：`measured`（OCCTO 实测）／`estimated`（本脚本摊分）。
**把推算值和实测值画成一样的点是在骗人**，前端必须能区分。

## ★ OCCTO 的「水力」大半是揚水（反直觉，先读）

    今市・塩原・安曇・新高瀬川・玉原・神流川・葛野川・沼原・奥矢作・奥美濃・
    新豊根・喜撰山・大河内・奥吉野・奥多々良木・俣野川・南原・本川・大平・天山・小丸川・京極

全是揚水。OCCTO 公表的是**调度对象**，可调度的水力主要就是揚水；
一般水力（尤其流込み式）跟河川流量走、不可调，本来就不在公表范围。

而 `area_jukyu` 把「水力」和「揚水」分成**两列**。
所以「OCCTO 水力 − 区域水力列」是**拿揚水减一般水力，公式根上就错**。

    OCCTO 41 座「水力」 = 揚水 29 + 一般水力 14
    一般水力的实测覆盖率因此只有 6.0%，不是把揚水算进去的 19–33%

## 各电源的站清单来源与可行性

| 电源 | 清单 | 规模核对 | 状态 |
|---|---|---|---|
| 水力 | `jepoc_hydro` 1,801 座（`output_kw`） | 48,965 MW | ✅ 坐标按容量覆盖 87.2% |
| 風力 | GEM 184 座 | 5,383 MW ≈ 日本实际 | ✅ GEM 自带坐标 |
| 地熱 | GEM 30 座 | 618 MW | ✅ |
| バイオマス | GEM 89 座 | 4,495 MW | ✅ |
| **太陽光** | — | GEM 35 GW **远低于实际** | ❌ **本脚本不做**，见下 |

### ❌ 太陽光为什么不做

不是数据不够好，是**概念上就没有「站」**：区域实绩的「太陽光発電実績」
含大量**住宅屋顶**，那部分不存在可标注的点位。GEM 收的是 utility-scale，
天然漏掉分布式。硬摊只会把住宅光伏的出力堆到大型电站的点上，是错的。

要做得拆两层：事业用（10kW以上）→ 站点；住宅（10kW未満）→ 市町村面。
`fit` 有这个分档，但画面需要**国土数値情報 N03 行政区域边界 —— 尚未取得**。
见 `TODO.md` S18。

## ⚠️ 会静默出错的坑

1. **区域名不一致**：`jepoc_hydro` 用「関東」「近畿」，OCCTO/`area_jukyu` 用
   「東京」「関西」。不映射会**静默丢掉两个最大的区域**。
2. **实测站匹配不能带区域约束**：jepoc 的区域是**地理地方**、OCCTO 的是**电力区域**。
   `下小鳥`（岐阜県但供北陸）就是这么漏掉的 —— 与 `FLOW_MATCHING.md` 误杀 4.1pt 同源。
3. **揚水与一般水力必须分开**，混算会双重计数。
4. **都道府県→电力区域有跨界县**：静岡（富士川以东属東京）、三重（南部属関西）、
   福井（嶺南属関西）。本脚本按主属归类，这些县的装机误差已知且不修 —— 摊分本就是推算。
5. **単位**：`jepoc_hydro` kW；GEM MW；`area_jukyu` MW平均（×0.5h=MWh）；
   `occto_unit` kWh/30分。

## 为什么没有按発電方式加权

`jepoc_hydro` 的 `method`（流込み式／調整池式／貯水池式）物理上日内曲线完全不同。
但**分配权重需要各方式的实测出力曲线，我们没有**，拍一组上去只是伪精确。
v1 按容量摊分，`method` 原样带进输出，等有验证手段再细化。

用法：
    python -m pipeline.structured.disaggregate
    python -m pipeline.structured.disaggregate --dump out.json
"""

import argparse
import csv
import io
import json
import logging
from collections import defaultdict
from pathlib import Path

import openpyxl

from pipeline.core import fetch, paths, report
from pipeline.structured.plant_coords import (GEM_GLOB, build_pool, pick,
                                              split_operator, valid)

log = logging.getLogger("powerjp.disagg")

JEPOC_TO_AREA = {"関東": "東京", "近畿": "関西"}
AREA_DIR = {"北海道": "hokkaido", "東北": "tohoku", "東京": "tokyo", "中部": "chubu",
            "北陸": "hokuriku", "関西": "kansai", "中国": "chugoku",
            "四国": "shikoku", "九州": "kyushu", "沖縄": "okinawa"}

# 都道府県 → 电力区域。⚠️ 跨界县按主属归类（见 docstring 坑 4）
PREF_TO_AREA = {
    "北海道": "北海道",
    "青森県": "東北", "岩手県": "東北", "宮城県": "東北", "秋田県": "東北",
    "山形県": "東北", "福島県": "東北", "新潟県": "東北",
    "茨城県": "東京", "栃木県": "東京", "群馬県": "東京", "埼玉県": "東京",
    "千葉県": "東京", "東京都": "東京", "神奈川県": "東京", "山梨県": "東京",
    "愛知県": "中部", "岐阜県": "中部", "三重県": "中部", "長野県": "中部",
    "静岡県": "中部",                        # ⚠️ 富士川以东实为東京
    "富山県": "北陸", "石川県": "北陸", "福井県": "北陸",   # ⚠️ 嶺南实为関西
    "大阪府": "関西", "京都府": "関西", "兵庫県": "関西", "奈良県": "関西",
    "滋賀県": "関西", "和歌山県": "関西",
    "鳥取県": "中国", "島根県": "中国", "岡山県": "中国",
    "広島県": "中国", "山口県": "中国",
    "徳島県": "四国", "香川県": "四国", "愛媛県": "四国", "高知県": "四国",
    "福岡県": "九州", "佐賀県": "九州", "長崎県": "九州", "熊本県": "九州",
    "大分県": "九州", "宮崎県": "九州", "鹿児島県": "九州",
    "沖縄県": "沖縄",
}

# 电源种 → (area_jukyu 的列名, GEM 的 Type)。水力单独处理（清单来自 jepoc）
GEM_KINDS = {
    "風力": ("風力発電実績", "wind"),
    "地熱": ("地熱", "geothermal"),
    "バイオマス": ("バイオマス", "bioenergy"),
}


def norm_date(s: str) -> str:
    s = (s or "").strip().replace("-", "/")
    return "/".join(str(int(x)) for x in s.split("/")) if s and s[0].isdigit() else s


def build_pref_index() -> list[tuple]:
    """坐标 → 都道府県 的最近邻索引，用 P03 的住所字符串建。

    ⚠️ 为什么不用 GEM 自己的地区列：日本行里 `Subnational unit` 只有 20/357 非空，
    `Major area` 186/357 且有拼写错误（`Hokkadio Prefecture`）。
    而 GEM **100% 有坐标**，P03 的 12,814 条**都带住所**（`島根県邑智郡美郷町…`），
    所以用坐标反查比用它自己的地区列可靠得多，且不需要引入新数据源。
    """
    import shapefile
    pts: list[tuple] = []
    base = paths.latest_version("ksj_p03", "P03-*") / "extracted" / "SHP"
    for shp in sorted(base.glob("*.shp")):
        r = shapefile.Reader(str(shp.with_suffix("")), encoding="cp932",
                             encodingErrors="replace")
        fl = [x[0] for x in r.fields[1:]]
        if "P03_0003" not in fl:          # 住所字段
            continue
        i = fl.index("P03_0003")
        for sr in r.iterShapeRecords():
            addr = str(sr.record[i] or "")
            pref = next((k for k in PREF_TO_AREA if addr.startswith(k)), None)
            if pref and sr.shape.points:
                x, y = sr.shape.points[0]
                pts.append((y, x, pref))
    return pts


def nearest_area(pt, index: list[tuple]) -> str | None:
    """最近的 P03 设施属于哪个都道府県 → 电力区域。"""
    if not valid(pt):
        return None
    lat, lon = float(pt[0]), float(pt[1])
    best, bd = None, 1e18
    for a, b, pref in index:
        d = (a - lat) ** 2 + ((b - lon) * 0.82) ** 2      # 粗略等距，够用
        if d < bd:
            best, bd = pref, d
    return PREF_TO_AREA.get(best) if best else None


def load_area(day: str) -> dict[str, dict[str, list[float]]]:
    """区域 → {列名: [48 个 MW]}。仅统一格式（FY2024+）。"""
    root = paths.raw("area_jukyu")
    want = ["水力", "揚水"] + [c for c, _ in GEM_KINDS.values()]
    out: dict[str, dict[str, list[float]]] = {}
    for jp, reg in AREA_DIR.items():
        cols: dict[str, list[float]] = defaultdict(list)
        for f in sorted((root / reg).glob("eria_jukyu_*.csv")):
            txt = f.read_text(encoding="cp932", errors="replace").splitlines()
            hi = next((i for i, l in enumerate(txt)
                       if l.replace('"', '').strip().upper().startswith("DATE")), None)
            if hi is None:
                continue
            for r in csv.DictReader(io.StringIO("\n".join(txt[hi:]))):
                if norm_date(r.get("DATE")) != day:
                    continue
                for c in want:
                    try:
                        cols[c].append(float((r.get(c) or "").replace(",", "")))
                    except ValueError:
                        cols[c].append(0.0)
        if any(any(v) for v in cols.values()):
            out[jp] = dict(cols)
    return out


def load_jepoc() -> list[dict]:
    d = json.loads((paths.raw("jepoc_hydro") / "plants.json").read_text(encoding="utf-8"))
    rows = d if isinstance(d, list) else next(v for v in d.values() if isinstance(v, list))
    out = []
    for r in rows:
        try:
            kw = float(str(r.get("output_kw", "")).replace(",", "") or 0)
        except ValueError:
            kw = 0.0
        method, form = str(r.get("method") or ""), str(r.get("form") or "")
        a = r.get("area_name", "")
        out.append({"name": r.get("name") or "", "operator": r.get("operator") or "",
                    "mw": kw / 1000.0, "form": form, "method": method,
                    "river": r.get("river_system") or "",
                    "area": JEPOC_TO_AREA.get(a, a),
                    # ⚠️ 不能丢揚水 —— OCCTO 的「水力」大半是揚水，丢了判不出实测
                    "pumped": ("揚水" in method) or ("揚水" in form)})
    return out


def load_gem(kind: str, index: list[tuple]) -> list[dict]:
    """GEM 的某类电源（operating），自带坐标 + 都道府県。"""
    wb = openpyxl.load_workbook(paths.latest_file("gem", GEM_GLOB), read_only=True)
    rows = wb["Power facilities"].iter_rows(values_only=True)
    hdr = next(rows)
    C = {h: i for i, h in enumerate(hdr) if h}
    pk = "Subnational unit (state, province)"
    out = []
    for r in rows:
        if str(r[C["Country/area"]]).strip() != "Japan":
            continue
        if str(r[C["Type"]] or "").strip().lower() != kind:
            continue
        if str(r[C["Status"]] or "").strip().lower() != "operating":
            continue
        try:
            mw = float(r[C["Capacity (MW)"]] or 0)
        except (TypeError, ValueError):
            mw = 0.0
        pt = (r[C["Latitude"]], r[C["Longitude"]])
        out.append({
            "name": str(r[C["Plant / Project name (local)"]]
                        or r[C["Plant / Project name"]] or "").split(",")[0].strip(),
            "operator": str(r[C["Operator(s)"]] or r[C["Owner(s)"]] or ""),
            "mw": mw, "form": "", "method": "", "river": "",
            "area": nearest_area(pt, index),
            "pt": pt if valid(pt) else None, "coord_source": "GEM",
        })
    return out


def load_occto_hydro(f: Path) -> dict[str, list[float]]:
    """实测水力：归一名（**不带区域**）→ 48 个 30 分値（MWh）。见坑 2。"""
    items = json.loads(f.read_text(encoding="utf-8"))["data"]["items"]
    out: dict[str, list[float]] = defaultdict(lambda: [0.0] * 48)
    for it in items:
        if (it.get("htdnNm") or "") != "水力":
            continue
        _, n = split_operator(it.get("htdnsNm", ""))
        if not n:
            continue
        for i, v in enumerate([it[k] for k in sorted(it) if k.startswith("htdnRslt")][:48]):
            try:
                out[n][i] += float(str(v).replace(",", "") or 0) / 1000.0
            except ValueError:
                pass
    return out


def spread(sites: list[dict], resid: list[float], kind: str) -> list[dict]:
    """把余量按装机容量摊到各站。"""
    tot = sum(s["mw"] for s in sites if s["mw"] > 0)
    out = []
    for s in sites:
        if s["mw"] <= 0:
            continue
        share = s["mw"] / tot if tot else 0.0
        out.append({**{k: s.get(k) for k in ("name", "operator", "area", "mw", "form",
                                             "method", "river", "pt", "coord_source")},
                    "kind": kind, "data_quality": "estimated",
                    "series": [round(r * share, 3) for r in resid]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="非调度电源 区域实绩 → 逐站摊分")
    ap.add_argument("--date")
    ap.add_argument("--dump")
    ap.add_argument("--show", type=int, default=8)
    args = ap.parse_args()
    fetch.setup_logging()

    files = sorted(paths.raw("occto_unit").rglob("unit_*.json"))
    target = next((f for f in files if args.date and args.date in f.name), files[-1])
    items = json.loads(target.read_text(encoding="utf-8"))["data"]["items"]
    day = norm_date(items[0]["tgtDate"])
    log.info("对象日 %s", day)

    area = load_area(day)
    pool = build_pool()
    pref_index = build_pref_index()
    log.info("都道府県索引 %d 点（来自 P03 住所）", len(pref_index))
    measured = load_occto_hydro(target)
    result: list[dict] = []
    stats: dict[tuple, dict] = {}

    # ── 水力（清单 jepoc，坐标靠匹配，有实测层）──────────────────────
    hyd = load_jepoc()
    for p in hyd:
        _, n = split_operator(p["name"])
        p["norm"] = n
        c = pick({"name": n, "fuel": "hydro", "area": p["area"], "op": ""}, pool.get(n, []))
        p["pt"], p["coord_source"] = (c["pt"], c["source"]) if c else (None, None)
        p["measured"] = n in measured
    log.info("水力 %d 座（揚水 %d）；实测对上 %d",
             len(hyd), sum(1 for p in hyd if p["pumped"]),
             sum(1 for p in hyd if p["measured"]))

    for jp, cols in sorted(area.items()):
        series = cols.get("水力") or []
        if not any(series):
            log.warning("%s「水力」列为空，跳过", jp)
            continue
        grp = [p for p in hyd if p["area"] == jp and not p["pumped"]]
        meas = [p for p in grp if p["measured"]]
        ms = [0.0] * len(series)
        for p in meas:
            s = measured.get(p["norm"]) or []
            for i in range(min(len(series), len(s))):
                ms[i] += s[i] * 2.0
        resid = [max(0.0, series[i] - ms[i]) for i in range(len(series))]
        result += spread([p for p in grp if not p["measured"]], resid, "水力")
        for p in meas:
            s = measured.get(p["norm"]) or []
            result.append({**{k: p.get(k) for k in ("name", "operator", "area", "mw",
                                                    "form", "method", "river", "pt",
                                                    "coord_source")},
                           "kind": "水力", "data_quality": "measured",
                           "series": [round(v * 2.0, 3) for v in s]})
        stats[(jp, "水力")] = {"区域": sum(series) / 2, "实测": sum(ms) / 2,
                              "摊分": sum(resid) / 2, "站": len(grp),
                              "实测座": len(meas),
                              "有坐标": sum(1 for p in grp if p["pt"]),
                              "坐标容量%": (sum(p["mw"] for p in grp if p["pt"])
                                        / sum(p["mw"] for p in grp) * 100) if grp else 0}

    # ── 風力／地熱／バイオマス（清单 GEM，自带坐标，无实测层）─────────
    for kind, (col, gt) in GEM_KINDS.items():
        sites = load_gem(gt, pref_index)
        noarea = sum(1 for s in sites if not s["area"])
        log.info("%s：GEM %d 座 %.0f MW（都道府県无法归区域的 %d 座）",
                 kind, len(sites), sum(s["mw"] for s in sites), noarea)
        for jp, cols in sorted(area.items()):
            series = cols.get(col) or []
            if not any(series):
                continue
            grp = [s for s in sites if s["area"] == jp]
            if not grp:
                log.warning("%s／%s：区域有实绩 %.0f MWh 但 GEM 无站 —— 该区域这类电源"
                            "画不出来", jp, kind, sum(series) / 2)
                continue
            result += spread(grp, series, kind)
            stats[(jp, kind)] = {"区域": sum(series) / 2, "实测": 0.0,
                                 "摊分": sum(series) / 2, "站": len(grp), "实测座": 0,
                                 "有坐标": sum(1 for s in grp if s["pt"]),
                                 "坐标容量%": (sum(s["mw"] for s in grp if s["pt"])
                                           / sum(s["mw"] for s in grp) * 100)
                                 if sum(s["mw"] for s in grp) else 0}

    print()
    print("═" * 96)
    print(f"{'区域':5} {'电源':10} {'区域MWh':>10} {'实测':>9} {'摊分':>10} {'实测%':>6} "
          f"{'站':>5} {'实测座':>5} {'有坐标':>5} {'坐标容量%':>9}")
    for (jp, kind), s in sorted(stats.items()):
        pct = s["实测"] / s["区域"] * 100 if s["区域"] else 0
        print(f"{jp:5} {kind:10} {s['区域']:>10,.0f} {s['实测']:>9,.0f} {s['摊分']:>10,.0f} "
              f"{pct:>5.1f}% {s['站']:>5} {s['实测座']:>5} {s['有坐标']:>5} "
              f"{s['坐标容量%']:>8.1f}%")
    print("═" * 96)

    print(f"\n{'电源':10} {'站':>6} {'有坐标':>6} {'当日MWh':>11} {'可上图%':>8} "
          f"{'実測MWh':>10}")
    for kind in ["水力"] + list(GEM_KINDS):
        rs = [r for r in result if r["kind"] == kind]
        if not rs:
            continue
        tot = sum(sum(r["series"]) / 2 for r in rs)
        pt = sum(sum(r["series"]) / 2 for r in rs if r["pt"])
        me = sum(sum(r["series"]) / 2 for r in rs if r["data_quality"] == "measured")
        print(f"{kind:10} {len(rs):>6} {sum(1 for r in rs if r['pt']):>6} "
              f"{tot:>11,.0f} {pt/tot*100 if tot else 0:>7.1f}% {me:>10,.0f}")

    print(f"\n各电源当日最大的 {args.show} 座：")
    for kind in ["水力"] + list(GEM_KINDS):
        rs = sorted((r for r in result if r["kind"] == kind),
                    key=lambda r: -sum(r["series"]))[:args.show]
        if not rs:
            continue
        print(f"  ── {kind}")
        for r in rs:
            q = "実測" if r["data_quality"] == "measured" else "推算"
            print(f"     {sum(r['series'])/2:>8,.0f} MWh [{q}] {r['area'] or '?':4} "
                  f"{r['name'][:16]:18} {r['mw']:>7,.1f}MW "
                  f"{'📍' if r['pt'] else '❌'} {str(r['operator'])[:20]}")

    by_kind = {}
    for kind in ["水力"] + list(GEM_KINDS):
        rs = [r for r in result if r["kind"] == kind]
        if not rs:
            continue
        t = sum(sum(r["series"]) / 2 for r in rs)
        by_kind[kind] = {
            "sites": len(rs), "with_coords": sum(1 for r in rs if r["pt"]),
            "mwh": round(t, 1),
            "mappable_pct": round(sum(sum(r["series"]) / 2 for r in rs if r["pt"])
                                  / t * 100, 1) if t else 0.0,
            "measured_mwh": round(sum(sum(r["series"]) / 2 for r in rs
                                      if r["data_quality"] == "measured"), 1),
        }
    report.emit(
        sites_total=len(result),
        measured=sum(1 for r in result if r["data_quality"] == "measured"),
        estimated=sum(1 for r in result if r["data_quality"] == "estimated"),
        with_coords=sum(1 for r in result if r["pt"]),
        by_kind=by_kind,
        # ⚠️ 太陽光有意不做（区域实绩含住宅屋顶，概念上没有「站」），见 TODO S18
        solar_excluded=True,
        output=args.dump,
    )

    if args.dump:
        Path(args.dump).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        log.info("写出 %d 条 → %s", len(result), args.dump)


if __name__ == "__main__":
    main()
