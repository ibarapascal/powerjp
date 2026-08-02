"""変電所の種別判定 —— 拓撲図の `sub` 節点に「何の所なのか」を付ける。

## なぜ必要か

AJG の拓撲図（`docs/data/built/all.json`）の節点は `name` と `kv` しか持たない。
縫合の過程で種別が落ちている。だから前端は 8,994 点をまとめて「変電所」と
呼んでいたが、**これは正しくない**。中身は：

- 配電用変電所（6.6kV へ落とす末端の設備。送電網ではない）
- 鉄道用変電所（き電。電力系統の運用とは関係ない）
- 需要家変電所（工場・大学の自家用）
- 架空/地中切替所（**そもそも「所」ではない**。ケーブルヘッドの地点）
- 開閉所・変換所・調相所

## 判定チェーン（信頼度の高い順・先に当たったものを採る）

| 順 | 根拠 | `stype_source` |
|---|---|---|
| 1 | 名前の明示的な語尾（開閉所・変換所・き電区分所・配電塔） | `name` |
| 2 | OSM の `substation=*` タグ（AJG 原データから座標で引き当て） | `osm` |
| 3 | 事業者名（JR・電鉄・交通局 → 鉄道用） | `operator` |
| 4 | 電圧からの**推定**（66/77kV → 配電用、110kV 以上 → 送電用） | `voltage` |
| 5 | 判らない | `none` |

⚠️ 4 は推定でしかない。66kV は配電用変電所の一次側が典型だが、
送電用変電所の二次側でもある。`stype_source` を見れば推定か実データか判る。

## 名前とタグ、どちらも単独では足りない

| | OSM タグ | 名前に語 |
|---|---:|---:|
| 開閉所 | 11 | 135 |
| 変換所 | 27 | 12 |

タグは付いていないことが多く、名前は付いていないものがある。**両方使う**。

## trunk（基幹系統かどうか）は種別とは別の軸

OCCTO の基幹送電線潮流データの端点に名前が出る設備は、基幹系統に属することが
実データで確認できる（383 か所）。これは「種別」ではないので `trunk` という
独立したフラグにする。種別が不明でも trunk が立っていれば送電系だと判る。

用法：
    python -m pipeline.structured.substation_type          # 判定して統計を出す
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
from collections import Counter, defaultdict

from pipeline.core import fetch, paths, report
from pipeline.structured.flow_match_probe import norm_station

log = logging.getLogger("powerjp.substation_type")

AJG_GLOB = "v*"          # ⚠️ 版を固定しない（CLAUDE.md「上流の版を書かない」）

# 種別コード → 日本語表記。前端の凡例と情報パネルはこれをそのまま使う
STYPE_JA = {
    "transmission": "送電用変電所",
    "distribution": "配電用変電所",
    "switching": "開閉所",
    "converter": "変換所（直流・周波数変換）",
    "traction": "鉄道用変電所",
    "industrial": "需要家変電所",
    "generation": "発電所構内変電所",
    "compensation": "調相設備",
    "transition": "架空/地中切替所",
    "unknown": "種別不明",
}

# OSM の `substation=*` → こちらのコード。
# `substation`/`yes` は「変電所である」としか言っていないので unknown 扱い
OSM_STYPE = {
    "transmission": "transmission",
    "distribution": "distribution",
    "minor_distribution": "distribution",
    "traction": "traction",
    "rail": "traction",
    "industrial": "industrial",
    "generation": "generation",
    "converter": "converter",
    "compensation": "compensation",
    "transition": "transition",
    "switching": "switching",
}

# 名前の語で判るもの。⚠️ 順序が意味を持つ（上から順に試す）
NAME_RULES = [
    (re.compile(r"開閉所|開閉塔|開閉基地|[（(]開[）)]|switching", re.I), "switching"),
    (re.compile(r"変換所|周波数変換|converter", re.I), "converter"),
    (re.compile(r"き電|饋電|electric区分所|区分所|信号所"), "traction"),
    (re.compile(r"配電塔"), "distribution"),
    (re.compile(r"調相"), "compensation"),
]

# 事業者名から鉄道用と判るもの
TRACTION_OP = re.compile(r"JR|ジェイアール|鉄道|電鉄|地下鉄|交通局|軌道|新幹線")
# 需要家（自家用）と判るもの
INDUSTRIAL_OP = re.compile(r"大学|製鉄|製鋼|製紙|化学|工業|製作所|セメント|病院")

# 電圧からの推定のしきい値。日本の配電用変電所の一次側は 66/77kV が標準
DIST_KV = {22, 33, 66, 77}
TRANS_KV_MIN = 110.0
# trunk 判定の足切り。OCCTO の基幹系統は 110kV 以上（中国 110 / 北陸 154 が下限）
TRUNK_KV_MIN = 110.0


def _ajg_root():
    base = paths.latest_version("ajg", AJG_GLOB) / "source"
    return [p for p in base.iterdir() if p.is_dir()][0]


def _centroid(g):
    c = g.get("coordinates")
    if not c:
        return None
    if g.get("type") == "Point":
        return float(c[1]), float(c[0])
    pts: list = []

    def walk(x):
        if isinstance(x[0], (int, float)):
            pts.append(x)
        else:
            for y in x:
                walk(y)

    walk(c)
    if not pts:
        return None
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)


def load_osm_attrs(src) -> list[dict]:
    """AJG が整えた変電所属性（`docs/data/substations.geojson`、6,962 件）。

    OSM の `substation=*` を `category` に、事業者を `operator` に入れてある。
    拓撲図の節点（8,994）とは件数が違うので、座標で引き当てる。
    """
    f = src / "docs" / "data" / "substations.geojson"
    out = []
    for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
        pt = _centroid(ft.get("geometry") or {})
        if not pt:
            continue
        p = ft.get("properties") or {}
        out.append({"lat": pt[0], "lon": pt[1],
                    "category": p.get("category") or p.get("substation_type") or "",
                    "operator": p.get("operator") or p.get("operator_short") or ""})
    log.info("OSM 属性 %d 件", len(out))
    return out


def load_occto_endpoints() -> set[str]:
    """OCCTO 基幹送電線の端点名（正規化済み）。ここに出れば基幹系統。"""
    root = paths.raw("occto_flow")
    files = sorted(root.rglob("chinai_kikan_jisseki_*.csv"))
    if not files:
        log.warning("occto_flow が無い。trunk 判定を飛ばす")
        return set()
    # エリアごとに最新の 1 日だけ見れば足りる（設備一覧は日で変わらない）
    latest: dict[str, object] = {}
    for f in files:
        latest[f.stem[-2:]] = f
    names: set[str] = set()
    for f in latest.values():
        text = f.read_bytes().decode("shift_jis", errors="replace")
        for r in csv.reader(io.StringIO(text)):
            if len(r) < 5:
                continue
            for part in re.split(r"[⇒→←⇔~〜]", r[4]):
                n = norm_station(part.strip())
                if n:
                    names.add(n)
    names.discard("潮流方向")          # ヘッダ行
    log.info("OCCTO 端点 %d 種（%d エリア）", len(names), len(latest))
    return names


def classify(nodes: list[dict], osm: list[dict], occto: set[str],
             snap_m: float = 300.0) -> dict[str, dict]:
    """拓撲図の節点 → `{node_id: {stype, stype_ja, stype_source, trunk}}`。"""
    cell = 0.005
    grid: dict[tuple, list[int]] = defaultdict(list)
    for i, o in enumerate(osm):
        grid[(int(o["lat"] / cell), int(o["lon"] / cell))].append(i)

    def nearest(lat: float, lon: float):
        best, bd = None, snap_m
        gi, gj = int(lat / cell), int(lon / cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in grid.get((gi + di, gj + dj), ()):
                    o = osm[i]
                    d = math.hypot((o["lat"] - lat) * 111_132.0,
                                   (o["lon"] - lon) * 111_320.0 * math.cos(math.radians(lat)))
                    if d < bd:
                        best, bd = o, d
        return best

    out: dict[str, dict] = {}
    for n in nodes:
        name = n.get("name") or ""
        kv = float(n.get("kv") or 0)
        near = nearest(float(n["lat"]), float(n["lon"]))
        stype = src = None

        # ① 名前の明示的な語
        for pat, t in NAME_RULES:
            if pat.search(name):
                stype, src = t, "name"
                break

        # ② OSM タグ
        if not stype and near:
            t = OSM_STYPE.get(near["category"])
            if t:
                stype, src = t, "osm"

        # ③ 事業者名
        if not stype and near:
            op = near["operator"]
            if TRACTION_OP.search(op) or TRACTION_OP.search(name):
                stype, src = "traction", "operator"
            elif INDUSTRIAL_OP.search(op) or INDUSTRIAL_OP.search(name):
                stype, src = "industrial", "operator"

        # ④ 電圧からの推定。⚠️ あくまで推定
        if not stype and kv:
            if kv in DIST_KV:
                stype, src = "distribution", "voltage"
            elif kv >= TRANS_KV_MIN:
                stype, src = "transmission", "voltage"

        if not stype:
            stype, src = "unknown", "none"

        out[n["id"]] = {
            "stype": stype,
            "stype_ja": STYPE_JA[stype],
            "stype_source": src,
            # 種別とは独立の軸。OCCTO 基幹潮流の端点に名前が出る＝基幹系統。
            # ⚠️ 名前だけで突き合わせるので同名の別所に付いてしまう
            #    （「福島」は 275kV の基幹と 66kV の配電用が 6 か所ある）。
            #    電圧で足切りする —— 基幹系統は 110kV 以上（中国 110 / 北陸 154 が下限）。
            "trunk": bool(occto) and kv >= TRUNK_KV_MIN and norm_station(name) in occto,
        }
    return out


def load_nodes(src) -> list[dict]:
    d = json.loads((src / "docs" / "data" / "built" / "all.json")
                   .read_text(encoding="utf-8"))
    return [n for n in d["nodes"]
            if str(n.get("sub")) == "1" and n.get("lat") is not None]


def main() -> None:
    fetch.setup_logging()
    src = _ajg_root()
    nodes = load_nodes(src)
    res = classify(nodes, load_osm_attrs(src), load_occto_endpoints())

    by_type = Counter(v["stype"] for v in res.values())
    by_src = Counter(v["stype_source"] for v in res.values())
    trunk = sum(1 for v in res.values() if v["trunk"])
    log.info("変電所節点 %d", len(res))
    for k, v in by_type.most_common():
        log.info("  %-14s %-22s %5d", k, STYPE_JA[k], v)
    log.info("判定根拠: %s", dict(by_src))
    log.info("基幹系統（OCCTO 端点に出現）: %d", trunk)

    report.emit(
        nodes=len(res),
        by_type=dict(by_type),
        by_source=dict(by_src),
        trunk=trunk,
        unknown_ratio=round(by_type["unknown"] / max(len(res), 1), 4),
    )


if __name__ == "__main__":
    main()
