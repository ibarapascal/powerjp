"""publish 层：structured 产物 → 前端直接消费的 GeoJSON。

## 这一层做什么

`structured/` 里的 JSON 是**我们自己的中间格式**（带匹配过程信息、诊断字段）。
前端要的是干净的 GeoJSON：几何 + 渲染需要的最小属性集。

    storage/structured/*.json  ──本脚本──>  storage/publish/*.geojson
                                                    ↓ 上传
                                              R2 → CDN → 前端

## ⛔ 几何绝不简化

不做 Douglas-Peucker，不按缩放级别抽稀，不合并顶点（`CLAUDE.md` publish 层硬约束）。
项目一开始就定了「所有的线都要是真实几何」，为省几百 KB 降级成折线近似，
等于把花最大力气拿到的东西又丢掉。

## 产物

| 文件 | 内容 | 关键属性 |
|---|---|---|
| `grid_lines.geojson` | AJG 全量送电线 | `kv`、`name` |
| `substations.geojson` | AJG 变电所 | `name`、`kv` |
| `flow_lines.geojson` | 潮流线（**带实测潮流的那些**） | `geometry_source`、`kv`、`km` |
| `plants.geojson` | 调度机组（**实测**） | `fuel`、`match_method`、`coord_source` |
| `sites.geojson` | 非调度电源（水力/風力/地熱/バイオマス） | `kind`、**`data_quality`** |

## ⚠️ 前端必须区分置信度

- `plants` 是 OCCTO 逐机组**实测**
- `sites` 里 `data_quality=estimated` 的是**按装机容量摊分推算的**
- `flow_lines` 的 `geometry_source=graph_route` 是**图上求解的路径**，不是官方走线图

**把推算值和实测值渲染成一样的东西是在骗人。**

用法：
    python -m pipeline.publish.geojson
"""

import json
import logging

from pipeline.core import fetch, paths, report
from pipeline.structured.plant_coords import AJG_TAG

log = logging.getLogger("powerjp.publish")

REGIONS = ["hokkaido", "tohoku", "tokyo", "chubu", "hokuriku",
           "kansai", "chugoku", "shikoku", "kyushu", "okinawa"]


def out_dir():
    d = paths.storage() / "publish"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write(name: str, features: list) -> int:
    p = out_dir() / name
    p.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                            ensure_ascii=False, separators=(",", ":")),
                 encoding="utf-8")
    n = p.stat().st_size
    log.info("%-24s %6d features  %8.1f KB", name, len(features), n / 1024)
    return n


def ajg_root():
    base = paths.raw("ajg", AJG_TAG) / "source"
    return [p for p in base.iterdir() if p.is_dir()][0]


def build_grid() -> tuple[int, int, int, int]:
    """AJG 的送电线与变电所 —— 地图的静态骨架。"""
    src = ajg_root()
    d = json.loads((src / "docs" / "data" / "built" / "all.json")
                   .read_text(encoding="utf-8"))

    lines = []
    for e in d["edges"]:
        path = e.get("path")
        if isinstance(path, str):
            try:
                path = json.loads(path)
            except ValueError:
                path = None
        if not (isinstance(path, list) and len(path) >= 2):
            continue
        lines.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         # GeoJSON 是 [lon, lat]，built/all.json 存的是 [lat, lon]
                         "coordinates": [[round(c[1], 6), round(c[0], 6)] for c in path]},
            "properties": {"kv": e.get("kv") or 0, "name": e.get("name") or ""},
        })
    n_lines = write("grid_lines.geojson", lines)

    subs = []
    for n in d["nodes"]:
        if str(n.get("sub")) != "1" or n.get("lat") is None:
            continue
        subs.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(float(n["lon"]), 6),
                                         round(float(n["lat"]), 6)]},
            "properties": {"name": n.get("name") or "", "kv": n.get("kv") or 0,
                           "region": n.get("region") or ""},
        })
    n_subs = write("substations.geojson", subs)
    return len(lines), n_lines, len(subs), n_subs


# ★ 電圧階級ごとの想定運用容量（MW）。
#
# ⚠️ **公式値ではない**。地内基幹送電線の熱容量・運用容量は公開されていない
#    （`occto_flow` の CSV に容量列は無く、`occto_capacity` は地域間連系線のもの）。
#    ここでは日本の一般的な回線構成を前提にした概算値を、実測潮流の最大値で
#    校正している（最大時の負荷率が 85〜105% に収まるように置いた）。
#
#    実測（当日平均|潮流|の最大）: 500kV 4,452 / 275kV 2,581 / 220kV 654 /
#                                187kV 277 / 132kV 255 MW
#
# ⚠️ 負荷率は**線路の公称電圧**で引くこと。区間の電圧ではない ——
#    潮流値は線路全体の値であり、経路が一時的に低電圧の辺を通っていても
#    その区間に線路全体の潮流が流れているわけではないため。
LINE_CAPACITY_MW = {
    500: 5000, 275: 2800, 220: 800, 187: 320,
    154: 800, 132: 300, 77: 200, 66: 200,
}


def _capacity(kv: float) -> float:
    """公称電圧 → 想定運用容量（MW）。未知の電圧は近い階級に丸める。"""
    if kv <= 0:
        return 0.0
    known = sorted(LINE_CAPACITY_MW)
    for k in known:
        if kv <= k:
            return float(LINE_CAPACITY_MW[k])
    return float(LINE_CAPACITY_MW[known[-1]])


def _kv(v) -> float:
    """電圧を数値へ。レイヤー間で型が揃っていないと式が壊れる。"""
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


def load_flow_mw() -> dict:
    """(エリア, 送電線名) → 当日の平均|潮流| (MW)。流光の色分けに使う。"""
    import csv
    import io
    root = paths.raw("occto_flow")
    files = sorted(root.rglob("*.csv"))
    if not files:
        return {}
    last = max(f.name.split("_")[-2] for f in files)
    out: dict = {}
    for f in [x for x in files if f"_{last}_" in x.name]:
        txt = f.read_text(encoding="cp932", errors="replace").splitlines()
        hi = next((i for i, l in enumerate(txt) if "送電線名" in l), None)
        if hi is None:
            continue
        for r in csv.DictReader(io.StringIO("\n".join(txt[hi:]))):
            nm = (r.get("送電線名") or "").strip()
            ar = (r.get("対象エリア") or "").strip()
            if not nm:
                continue
            vals = []
            for k, v in r.items():
                if k and ":" in str(k):          # 00:00 … 23:30 の各コマ
                    try:
                        vals.append(abs(float(str(v).replace(",", ""))))
                    except (TypeError, ValueError):
                        pass
            if vals:
                out[(ar, nm)] = sum(vals) / len(vals)
    log.info("潮流値 %d 本ぶん読込（対象日 %s）", len(out), last)
    return out


# 直線代用を許す最大長（km）。これ以下なら「近接した接続の欠落」とみなす。
# ⚠️ ユーザー方針（2026-08-02）：
#   「実際は近い場所で単に 500kV が繋がっていないだけ、かつ中間が OSM 側で
#     未分類なら残してよい。明確に小さい線路なら描いてはいけない」
SHORT_GAP_KM = 1.5
# 段の電圧が線路公称電圧のこの比率を下回ったら、経路が誤っている疑いが強い
KV_MISMATCH_RATIO = 0.5


def _seg_km(coords) -> float:
    import math

    def hav(a, b):
        la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
        h = (math.sin((la2 - la1) / 2) ** 2
             + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
        return 2 * 6371 * math.asin(math.sqrt(h))

    return sum(hav(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _seg_quality(sg, line_kv: float, km: float) -> str:
    """区間の信頼度。**描いてよいのは real / short_gap だけ**。

    | 値 | 意味 | 描画 |
    |---|---|---|
    | `real` | 実幾何あり・電圧も整合 | ✅ |
    | `short_gap` | 幾何が無く直線代用だが < 1.5km。近接した接続欠落とみなす | ✅ |
    | `straight` | 幾何が無く、しかも長い。**実在しない直線**になる | ❌ |
    | `kv_mismatch` | 段の電圧が線路公称の半分未満。経路が別の線に迷い込んでいる | ❌ |

    ⚠️ 段の電圧が 0（OSM 未分類）は誤りとみなさない。
       「不明」であって「低圧と判明している」ではないため。
    """
    skv = float(sg.get("kv") or 0)
    if line_kv > 0 and skv > 0 and skv < line_kv * KV_MISMATCH_RATIO:
        return "kv_mismatch"
    if not sg.get("has_geom"):
        return "short_gap" if km <= SHORT_GAP_KM else "straight"
    return "real"


def build_flow() -> int:
    """★ **セグメント単位**で出す。

    一本の潮流線は複数の辺をつないだ経路で、途中で電圧が変わることがある
    （実測で 335 本中 12 本）。線路全体を 1 つの電圧で描くと、
    下の送電線と幅が合わず「枠が付いている」ように見える。
    段ごとに実際の kv を持たせ、前端では送電線と同じ幅式を適用する。
    """
    p = paths.structured("lines") / "lines.json"
    if not p.is_file():
        log.warning("%s 不存在 —— 先跑 structured_lines", p)
        return 0
    mw_map = load_flow_mw()
    feats = []
    stat: dict = {}
    dropped_km = 0.0
    for r in json.loads(p.read_text(encoding="utf-8")):
        segs = r.get("segments") or []
        mw = mw_map.get((r["area"], r["line"]))
        for i, sg in enumerate(segs):
            c = sg.get("coords") or []
            if len(c) < 2:
                continue
            km = _seg_km(c)
            q = _seg_quality(sg, _kv(r["kv"]), km)
            stat[q] = stat.get(q, 0) + 1
            # ⛔ 誤った幾何は**出力しない**。線が途切れても、実在しない線を
            #    描くよりはよい（ユーザー方針：「不能画错的」）。
            if q in ("straight", "kv_mismatch"):
                dropped_km += km
                continue
            feats.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[round(x[1], 6), round(x[0], 6)] for x in c]},
                "properties": {
                    "line": r["line"], "area": r["area"],
                    # ⚠️ 段の実電圧。線路全体の公称電圧ではない
                    "kv": _kv(sg.get("kv")),
                    "line_kv": _kv(r["kv"]),
                    "from": r["from"], "to": r["to"], "km": r["km"],
                    # 当日の平均|潮流|
                    "mw": round(mw, 1) if mw is not None else None,
                    # ★ 負荷率 = |潮流| ÷ 想定運用容量。流光の色はこれで決まる。
                    #   ⚠️ 分母は**線路の公称電圧**由来（区間の電圧ではない）
                    "util": (round(mw / _capacity(_kv(r["kv"])), 3)
                             if mw is not None and _capacity(_kv(r["kv"])) > 0 else None),
                    "capacity_mw": _capacity(_kv(r["kv"])),
                    "seg": i,
                    "seg_km": round(km, 2),
                    # real / short_gap のみ出力される
                    "seg_quality": q,
                    # ⚠️ 图上求解的路径，不是官方走线图
                    "geometry_source": r["geometry_source"]},
            })
    # ★ 同じ幾何を共有する区間に印を付ける（描き分けはしない）
    #
    # 経路探索は両端しか知らないため、別々の線路が同じ辺に載ることがある。
    # 日本は**同塔並架**が多く、幾何が重なるのが物理的に正しい場合もあるので、
    # データからは「求解の誤り」と「実際の並架」を区別できない。
    #
    # ⚠️ ユーザー方針（2026-08-02）：**オフセットで描き分けない**。
    #    線幅は電圧で一意に決まる決まりなので、オフセット量を線幅に
    #    追従させると幅の定義が二重になる。重なったままでよい。
    #    ただしクリックしたとき何本共有しているかは分かるようにする。
    share: dict = {}
    for f in feats:
        c = f["geometry"]["coordinates"]
        k = (tuple(c[0]), tuple(c[-1]))
        k = k if k[0] <= k[1] else (k[1], k[0])
        share.setdefault(k, []).append(f)
    for group in share.values():
        if len(group) < 2:
            continue
        names = sorted({g["properties"]["line"] for g in group})
        for f in group:
            others = [n for n in names if n != f["properties"]["line"]]
            f["properties"]["shared_with"] = "、".join(others) if others else None
            f["properties"]["shared_count"] = len(group)

    n_shared = sum(1 for f in feats if f["properties"].get("shared_count"))
    log.info("区間の内訳 %s / 除外した長さ %.0f km / 幾何共有 %d 区間",
             stat, dropped_km, n_shared)
    return write("flow_lines.geojson", feats)


def build_plants() -> int:
    p = paths.structured("plants") / "plants.json"
    if not p.is_file():
        log.warning("%s 不存在 —— 先跑 structured_plants", p)
        return 0
    feats = []
    for r in json.loads(p.read_text(encoding="utf-8")):
        if r.get("lat") is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "name": r["name"], "operator": r["operator"], "area": r["area"],
                "fuel": r["fuel"], "kwh": r["kwh_sample_day"],
                "data_quality": "measured",          # OCCTO 逐机组实测
                "coord_source": r["coord_source"], "match_method": r["match_method"],
            },
        })
    return write("plants.geojson", feats)


def build_sites() -> int:
    p = paths.structured("disaggregated") / "sites.json"
    if not p.is_file():
        log.warning("%s 不存在 —— 先跑 structured_disaggregated", p)
        return 0
    feats = []
    for r in json.loads(p.read_text(encoding="utf-8")):
        pt = r.get("pt")
        if not pt:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(float(pt[1]), 6), round(float(pt[0]), 6)]},
            "properties": {
                "name": r["name"], "operator": r["operator"], "area": r["area"],
                "kind": r["kind"], "mw": r["mw"], "method": r["method"],
                # ⚠️ estimated = 按装机容量摊分推算，不是实测
                "data_quality": r["data_quality"],
                "mwh": round(sum(r["series"]) / 2, 1),
            },
        })
    return write("sites.geojson", feats)


def main() -> None:
    fetch.setup_logging()
    n_line, b_line, n_sub, b_sub = build_grid()
    b_flow = build_flow()
    b_plant = build_plants()
    b_site = build_sites()
    total = b_line + b_sub + b_flow + b_plant + b_site
    log.info("合计 %.1f MB（未压缩）", total / 1024 / 1024)
    report.emit(
        path=str(out_dir()), bytes=total,
        size_human=report.human_bytes(total),
        grid_lines=n_line, substations=n_sub,
        simplified=False,          # ⛔ 硬约束：几何绝不简化
        note="GeoJSON 未压缩；CDN 侧 gzip/br 后约为 1/3",
    )


if __name__ == "__main__":
    main()
