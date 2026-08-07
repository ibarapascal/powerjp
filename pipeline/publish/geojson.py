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
| `substations.geojson` | AJG 变电所ほか | `name`、`kv`、**`stype`**、`trunk` |
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

import argparse
import json
import logging
from collections import Counter

from pipeline.core import fetch, paths, report
from pipeline.publish import spec
from pipeline.structured import substation_type
from pipeline.structured.plant_coords import AJG_GLOB

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
    base = paths.latest_version("ajg", AJG_GLOB) / "source"
    return [p for p in base.iterdir() if p.is_dir()][0]


# 2 点しかない線をどこまで許すか（km）。これを超えるものは直線近似とみなす
STRAIGHT_MAX_KM = 2.0


def _line_km(coords) -> float:
    """[lon, lat] の並びの長さ（km）。"""
    import math

    def hav(a, b):
        la1, lo1, la2, lo2 = map(math.radians, (a[1], a[0], b[1], b[0]))
        h = (math.sin((la2 - la1) / 2) ** 2
             + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
        return 2 * 6371 * math.asin(math.sqrt(h))

    return sum(hav(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def build_grid() -> tuple[int, int, int, int]:
    """送電線と変電所。地図の静的な骨格。

    ## ★ 線は**原データ**から出す。拓撲図からではない

    AJG は 2 つのものを配っている：

        data/{region}_lines.geojson   OSM の原抽出        40,931 本
        docs/data/built/all.json      AJG が組んだ拓撲図   14,048 本（幾何あり）

    以前は拓撲図から地図用の線を作っていた。だが拓撲図は**連結性のために
    作られたもの**で、繋がらない線は捨てられている（架空線の 18%、
    地下ケーブルの 56%）。地図に描くだけなら繋がっている必要は無い。

    「描けるか」と「拓撲が計算できるか」は別問題。
    描画は原データ（欠けが少ない）、経路探索は拓撲図（連結性がある）を使う。

    ⚠️ どちらなのかは `in_topology` で示す。拓撲図に無い線は
    潮流の経路探索には使えない —— 地図に見えていても計算には入らない。
    """
    src = ajg_root()
    d = json.loads((src / "docs" / "data" / "built" / "all.json")
                   .read_text(encoding="utf-8"))

    # 拓撲図に含まれる線分（無向）。原データの各線がここに居るか判定する
    def key(c):
        return (round(float(c[0]), 5), round(float(c[1]), 5))

    topo: set = set()
    for e in d["edges"]:
        path = e.get("path")
        if isinstance(path, str):
            try:
                path = json.loads(path)
            except ValueError:
                path = None
        if not isinstance(path, list):
            continue
        for i in range(len(path) - 1):
            a, b = key(path[i]), key(path[i + 1])
            topo.add((a, b) if a <= b else (b, a))

    lines = []
    n_straight = 0
    for f in sorted((src / "data").glob("*_lines*.geojson")):
        region = f.stem.split("_")[0]
        for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
            pr = ft.get("properties", {}) or {}
            gm = ft.get("geometry") or {}
            if gm.get("type") == "LineString":
                parts = [gm.get("coordinates")]
            elif gm.get("type") == "MultiLineString":
                parts = gm.get("coordinates") or []
            else:
                continue
            for coords in parts:
                if not isinstance(coords, list) or len(coords) < 2:
                    continue
                # ⛔ 2 点しかないのに長い線は、実際の走線ではなく
                #    「両端を直線で結んだだけ」の可能性が高い。地図に出さない。
                #    ⚠️ 2 点でも短ければ鉄塔 1 径間の実データなので残す
                #    （実測：2 点の線 16,675 本のうち 2km 超はわずか 7 本）。
                if len(coords) == 2 and _line_km(coords) > STRAIGHT_MAX_KM:
                    n_straight += 1
                    continue
                # 拓撲図に居る線分の割合。過半なら「拓撲に入っている」とみなす
                tot = hit = 0
                for i in range(len(coords) - 1):
                    a = key([coords[i][1], coords[i][0]])
                    b = key([coords[i + 1][1], coords[i + 1][0]])
                    tot += 1
                    hit += ((a, b) if a <= b else (b, a)) in topo
                lines.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString",
                                 "coordinates": [[round(c[0], 6), round(c[1], 6)]
                                                 for c in coords]},
                    "properties": {
                        "kv": _kv_tag(pr.get("voltage")),
                        "name": pr.get("name:ja") or pr.get("name") or "",
                        # line = 架空、cable = 地下・水底
                        "power": pr.get("power") or "line",
                        "underground": (pr.get("power") == "cable"
                                        or pr.get("location") == "underground"),
                        # ⚠️ False の線は地図に見えても潮流計算には入らない
                        "in_topology": tot > 0 and hit / tot >= 0.5,
                        "region": region,
                    },
                })
    if n_straight:
        log.info("2 点だけの長い線を除外: %d 本（実走線ではなく直線近似）", n_straight)
    n_lines = write("grid_lines.geojson", lines)

    # 種別（送電用 / 配電用 / 開閉所 / 鉄道用 …）を付ける。
    # ⚠️ 拓撲図の節点は name と kv しか持たない。種別は別途復元する
    #    （`structured/substation_type.py`）。前端で色は分けないが、
    #    「8,994 か所ぜんぶ変電所」という不正確な説明をしないために要る。
    sub_nodes = [n for n in d["nodes"]
                 if str(n.get("sub")) == "1" and n.get("lat") is not None]
    stype = substation_type.classify(
        sub_nodes,
        substation_type.load_osm_attrs(src),
        substation_type.load_occto_endpoints(),
    )

    subs = []
    for n in sub_nodes:
        t = stype[n["id"]]
        subs.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(float(n["lon"]), 6),
                                         round(float(n["lat"]), 6)]},
            "properties": {"name": n.get("name") or "", "kv": n.get("kv") or 0,
                           "region": n.get("region") or "",
                           "stype": t["stype"], "stype_ja": t["stype_ja"],
                           # 実データか推定かが判る。前端の情報パネルに出す
                           "stype_source": t["stype_source"],
                           "trunk": t["trunk"]},
        })
    n_subs = write("substations.geojson", subs)
    log.info("変電所の種別: %s",
             dict(Counter(f["properties"]["stype"] for f in subs).most_common()))
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


def _kv_tag(v) -> float:
    """OSM の voltage タグ（V 単位・`66000;154000` のような複数値）→ kV。"""
    if not v:
        return 0.0
    best = 0.0
    for part in str(v).replace(",", ";").split(";"):
        try:
            best = max(best, float(part.strip()))
        except ValueError:
            pass
    return round(best / 1000, 1) if best else 0.0


def _kv(v) -> float:
    """電圧を数値へ。レイヤー間で型が揃っていないと式が壊れる。"""
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


# 前端に渡す時系列の長さ（時間）。48 コマ/日なので 72h = 144 コマ。
# ⚠️ 「当日」で切ってはいけない —— 日付が変わった直後は数コマしか無い。
#    暦日ではなく**直近 N 時間**で切る。
# 時間軸の長さ（時間）。⚠️ ここを変えたら前端の目盛りは**自動で追従する**
# （`TimeSlider` が stamps の長さからラベル間隔を決める）。
# 2026-08-07：72 → 120。機組の欠測（T+1 公表なので 15.5〜39.5 時間）が
# 窓に占める割合を下げるため。代価は dynamic 層 +516KB。
FLOW_WINDOW_H = 120
SLOTS_PER_DAY = 48


def load_flow_slots() -> tuple[dict, list[str]]:
    """(エリア, 送電線名) → **符号つき 30 分コマの並び**、と対応する時刻ラベル。

    ## ⚠️ 「潮流方向(正方向)」は**基準**であって実際の向きではない

    OCCTO は各線路に基準の向きを決めて、その向きを正として値を出す。
    **負の値は逆向きに流れていることを意味する**。

    ## ⚠️ 平均を取ってはいけない。向きは時間で変わる

    実測（2026-07-25〜31 の 7 日）: **464 本中 184 本（40%）が窓の中で向きを変える**。
    少数側の割合は中央値 13%、最大 35%。当日平均の符号で幾何を反転させると、
    その 40% は一日の何割かの時間で**逆向きに描かれる**ことになる。

    したがって：
    - 幾何は OCCTO の基準方向のまま。**反転しない**
    - ここでは符号つきの値をコマごとに残す
    - 流光の向きは前端が「今どのコマか」を見て決める

    戻り値の `slots` は `stamps` と同じ長さ・同じ順序。値が無いコマは `None`。
    """
    import csv
    import io
    root = paths.raw("occto_flow")
    files = sorted(root.rglob("*.csv"))
    if not files:
        return {}, []

    # ---- 対象日 ----
    # ⚠️ **窓は「直近 FLOW_WINDOW_H 時間」であって「直近 N 日」ではない**
    #    （REQUIREMENTS T5「浮动过去 24 48 或者 72」）。
    #    日単位で切ると窓の頭が日境界に貼り付き、時間が進んでも動かない。
    #    例：72h のつもりが 08-04 00:00〜08-06 21:00（69.5h）になる。
    #    なので**1 日多く**読み込んでおき、下で末尾から 144 コマだけ残す。
    days = sorted({f.name.split("_")[-2] for f in files})
    n_days = -(-FLOW_WINDOW_H // 24) + 1             # 切り上げ + 余分に 1 日
    days = days[-n_days:]
    stamps = [f"{d} {h:02d}:{m:02d}"
              for d in days for h in range(24) for m in (0, 30)]
    idx = {s: i for i, s in enumerate(stamps)}

    out: dict = {}
    for f in files:
        day = f.name.split("_")[-2]
        if day not in days:
            continue
        txt = f.read_text(encoding="cp932", errors="replace").splitlines()
        hi = next((i for i, l in enumerate(txt) if "送電線名" in l), None)
        if hi is None:
            continue
        for r in csv.DictReader(io.StringIO("\n".join(txt[hi:]))):
            nm = (r.get("送電線名") or "").strip()
            ar = (r.get("対象エリア") or "").strip()
            if not nm:
                continue
            slots = out.setdefault((ar, nm), [None] * len(stamps))
            for k, v in r.items():
                if not k or ":" not in str(k):       # 00:00 … 23:30 の各コマ
                    continue
                j = idx.get(f"{day} {str(k).strip()}")
                if j is None:
                    continue
                try:
                    slots[j] = round(float(str(v).replace(",", "")), 1)
                except (TypeError, ValueError):
                    pass

    # ---- 末尾の「まだ来ていないコマ」を落とす ----
    # stamps は日単位で作るので、当日ぶんの未到来コマが必ず末尾に null で残る。
    # 描けないので残しても無意味なうえ、前端の式が null を含む配列を嫌う
    # （LESSONS #42）。窓の途中の欠測はデータの事実なので**残す**。
    last = max((i for s in out.values() for i, v in enumerate(s) if v is not None),
               default=-1)
    if 0 <= last + 1 < len(stamps):
        log.info("末尾の未到来コマを %d 個落とした（%s 以降）",
                 len(stamps) - last - 1, stamps[last + 1])
        stamps = stamps[:last + 1]
        for k in out:
            out[k] = out[k][:last + 1]

    # ---- 窓を「直近 FLOW_WINDOW_H 時間」ちょうどに切り詰める ----
    # 末尾（最新の実測コマ）から数えて必要なぶんだけ残す。これで窓の頭が
    # 時間の経過に合わせて滑る。⚠️ 上で 1 日多く読んでいるのはこのため。
    want = FLOW_WINDOW_H * SLOTS_PER_DAY // 24
    if len(stamps) > want:
        log.info("窓を直近 %d 時間に揃えた：先頭の %d コマを落とす（%s 〜）",
                 FLOW_WINDOW_H, len(stamps) - want, stamps[len(stamps) - want])
        stamps = stamps[-want:]
        for k in out:
            out[k] = out[k][-want:]

    filled = sum(1 for s in out.values() for v in s if v is not None)
    n_flip = sum(1 for s in out.values()
                 if any(v and v > 0 for v in s) and any(v and v < 0 for v in s))
    log.info("潮流 %d 本 × %d コマ（%s 〜 %s）／実値 %d ／窓内で向きが変わる線 %d 本 (%.0f%%)",
             len(out), len(stamps), stamps[0], stamps[-1], filled,
             n_flip, n_flip / max(len(out), 1) * 100)
    return out, stamps


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


def build_flow() -> tuple[int, list[str]]:
    """★ **セグメント単位**で出す。

    一本の潮流線は複数の辺をつないだ経路で、途中で電圧が変わることがある
    （実測で 335 本中 12 本）。線路全体を 1 つの電圧で描くと、
    下の送電線と幅が合わず「枠が付いている」ように見える。
    段ごとに実際の kv を持たせ、前端では送電線と同じ幅式を適用する。

    ## ⚠️ 幾何は反転しない（2026-08-02 に方針変更）

    以前は「当日平均の符号が負なら幾何を反転」していた。当日平均で 1 本 1 向きに
    決めていたから成り立っていた話で、**コマ単位にすると破綻する** ——
    実測で 464 本中 184 本（40%）が窓の中で向きを変える。

    幾何は OCCTO の基準方向のまま出し、**向きは前端がコマごとに決める**。
    """
    p = paths.structured("lines") / "lines.json"
    if not p.is_file():
        log.warning("%s 不存在 —— 先跑 structured_lines", p)
        return 0, []
    slot_map, stamps = load_flow_slots()
    feats = []
    stat: dict = {}
    dropped_km = 0.0
    for r in json.loads(p.read_text(encoding="utf-8")):
        segs = r.get("segments") or []
        # ⚠️ **None を出さない。長さぶんの null 配列にする**（LESSONS #43）
        #    実測が 1 コマも無い線が 684 区間中 12 ある。そこを None のまま
        #    出すと、前端の式 `['array', ['get','slots_mw']]` が
        #    「null は配列ではない」で評価に失敗し、**要素ごと・タイルごとに**
        #    warning を出す（地図を動かすと際限なく増える）。
        #    空の配列でも `at` が範囲外になるので、長さを揃えるのが正解。
        slots = slot_map.get((r["area"], r["line"])) or [None] * len(stamps)
        # 代表値は**最後に値のあるコマ**（＝最新断面）。凡例や初期表示用
        latest = next((v for v in reversed(slots or []) if v is not None), None)
        frm, to = r["from"], r["to"]
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
                    # ⚠️ OCCTO の基準方向のまま。実際の向きはコマの符号で決まる
                    "from": frm, "to": to,
                    # ★ 符号つき 30 分コマの並び。長さは全 feature 共通で、
                    #   `flow_stamps.json` の時刻ラベルと一対一で対応する。
                    #   正 = from→to、負 = to→from。値が無いコマは null。
                    "slots_mw": slots,
                    # 最新断面の値（初期表示・凡例用）。これも符号つき
                    "mw_latest": latest,
                    "km": r["km"],
                    # ★ 負荷率の分母。負荷率そのものはコマ依存なので前端で割る。
                    #   ⚠️ 分母は**線路の公称電圧**由来（区間の電圧ではない）
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

    return write("flow_lines.geojson", feats), stamps


def write_stamps(stamps: list[str], unit_last_idx: int) -> None:
    """時刻ラベルを別ファイルに出す。

    全 feature で共通なので GeoJSON には埋めない ——
    684 feature × 139 個の文字列を重複させると数 MB 無駄になる。

    ## `unit_last_idx`：機組の実測が入っている最後のコマ

    ⚠️ **潮流より手前で終わる。** `occto_unit` は T+1 公表なので、
    潮流に値があるコマでも機組は丸一日ぶん欠測になる。

    ここで出すのは**事実だけ**（どこまで機組のデータがあるか）。
    「既定でどのコマに止めるか」「欠測区間をどう描くか」は表示側の判断なので
    前端が決める。publish が UI の既定値を決め打ちすると、
    見せ方を変えるたびにパイプラインを回し直す羽目になる。
    """
    (out_dir() / "flow_stamps.json").write_text(
        json.dumps({"stamps": stamps, "window_h": FLOW_WINDOW_H,
                    "slots_per_day": SLOTS_PER_DAY,
                    "unit_last_idx": unit_last_idx}, ensure_ascii=False),
        encoding="utf-8")
    log.info("flow_stamps.json      %d コマ（%s 〜 %s）／機組の実測は %s まで",
             len(stamps), stamps[0] if stamps else "-", stamps[-1] if stamps else "-",
             stamps[unit_last_idx] if 0 <= unit_last_idx < len(stamps) else "-")


def _last_mw(slots):
    """最新コマの出力（MW）。末尾が欠測なら遡って最後の実測値を採る。"""
    if not slots:
        return None
    for v in reversed(slots):
        if v is not None:
            return float(v)
    return None


def load_unit_slots(stamps: list[str]) -> dict:
    """(事業者, 正規化名) → 30 分コマの出力（MW）。**潮流と同じ時間軸**に載せる。

    ## なぜ publish 層で読み直すのか

    `structured/plants/plants.json` の `slots_mw` は**当日 48 コマだけ**。
    あれは「その日の最大出力 ≦ 設備容量か」を確かめるためのもので、
    表示用ではない（`plant_coords.load_occto()` は 1 日しか読まない）。

    時間軸を潮流と共有するには同じ `stamps` に揃える必要があるので、
    ここで日をまたいで組み直す。⚠️ `stamps` と長さ・順序が一対一。

    キーは `split_operator()` の結果 —— structured 側と同じ規則で割らないと
    「松浦」「橘湾」のような同地名別会社が混ざる。
    """
    from pipeline.structured.plant_coords import split_operator

    days = sorted({s.split(" ")[0] for s in stamps})
    idx = {s: i for i, s in enumerate(stamps)}
    files = sorted(paths.raw("occto_unit").rglob("unit_*.json"))
    out: dict = {}
    n_day = 0
    for f in files:
        day = f.stem.replace("unit_", "")
        if day not in days:
            continue
        n_day += 1
        items = json.loads(f.read_text(encoding="utf-8"))["data"]["items"]
        for it in items:
            op, n = split_operator(it.get("htdnsNm", ""))
            if not n:
                continue
            slots = out.setdefault((op, n), [None] * len(stamps))
            # ⚠️ キーは**連番ではなく時刻**：`htdnRslt0030` 〜 `htdnRslt2400`。
            #    しかも指すのは区間の**終わり**（`0030` は 00:00〜00:30 の実績、
            #    `2400` は 23:30〜24:00）。潮流 CSV の列は区間の**始まり**
            #    （00:00 … 23:30）なので、30 分引いて始点に合わせる。
            #    連番として読むと `0030` だけが 0〜47 に収まり、
            #    **1 コマしか埋まらない**（実際にそうなった）。
            for k in it:
                if not k.startswith("htdnRslt"):
                    continue
                hhmm = k[len("htdnRslt"):]
                if len(hhmm) != 4 or not hhmm.isdigit():
                    continue
                mins = int(hhmm[:2]) * 60 + int(hhmm[2:]) - 30     # 区間の始点へ
                if not 0 <= mins < 24 * 60:
                    continue
                j = idx.get(f"{day} {mins // 60:02d}:{mins % 60:02d}")
                if j is None:
                    continue
                try:
                    v = int(str(it[k]).replace(",", "") or 0)
                except ValueError:
                    continue
                # kWh/30分 → MW（1 コマ 0.5h なので ×2、kW→MW で ÷1000）
                slots[j] = round((slots[j] or 0.0) + v * 2 / 1000.0, 2)
    log.info("機組の出力 %d 系列 × %d コマ（%d 日ぶん）", len(out), len(stamps), n_day)
    return out


# 扇形の段階を 1 文字にする表。0〜20 段（`pieIcons.ts` の CF_STEPS と一致）
# ⚠️ 欠測は '-'。0 段（停止中）と混ぜないこと
FAN_CHARS = "ABCDEFGHIJKLMNOPQRSTU"
FAN_MISSING = "-"


def fan_steps(slots, cap) -> str:
    """コマごとの「出力÷容量」を 1 文字ずつに畳んだ文字列。

    ## なぜ配列ではなく文字列なのか（LESSONS #43）

    扇形は `icon-image` で選ぶが、これは **layout プロパティ**で、
    worker のタイル配置時に**シリアライズ済みの属性**に対して評価される。
    そこでは配列は JSON 文字列になっているので、式で `at` を使うと
    毎タイル・毎要素で型アサーションに失敗して warning を出し続ける
    （地図を動かすたびに増える）。

    文字列なら serialise しても文字列のままなので、
    `['slice', ['get','fsteps'], i, i+1]` で 1 文字取れば layout でも安全。

    ⚠️ `slots_mw`（配列）は**残す** —— 情報パネルが MW を出すのに要る。
       あちらは JS で読むので `JSON.parse` すればよい。
    """
    c = max(float(cap or 0), 1.0)
    out = []
    for v in slots or []:
        if v is None:
            out.append(FAN_MISSING)
            continue
        k = int(min(abs(float(v)) / c, 1.0) * (len(FAN_CHARS) - 1) + 0.5)
        out.append(FAN_CHARS[max(0, min(len(FAN_CHARS) - 1, k))])
    return "".join(out)


def build_plants(stamps: list[str] | None = None) -> tuple[int, int]:
    """戻り値は (バイト数, **実測が入っている最後のコマ番号**)。

    2 つ目は `write_stamps` の `default_idx` に使う。機組は T+1 公表なので
    潮流より遅れる ——「潮流はあるが機組は無い」コマを既定にしないため。
    """
    p = paths.structured("plants") / "plants.json"
    if not p.is_file():
        log.warning("%s 不存在 —— 先跑 structured_plants", p)
        return 0, -1
    # ★ 潮流と**同じ時間軸**に載せる。スライダーを 1 本で共有するため
    unit = load_unit_slots(stamps) if stamps else {}
    n_hit = 0
    feats = []
    for r in json.loads(p.read_text(encoding="utf-8")):
        if r.get("lat") is None:
            continue
        # structured 側の 48 コマではなく、時間軸に揃えた系列を使う
        slots = unit.get((r["operator"], r["name"])) if stamps else None
        if slots is None:
            slots = r.get("slots_mw")
        else:
            n_hit += 1
        # ⚠️ ここも None を出さない（上と同じ理由）
        if slots is None and stamps:
            slots = [None] * len(stamps)
        cap = r.get("capacity_mw")
        latest = _last_mw(slots)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "name": r["name"], "operator": r["operator"], "area": r["area"],
                "fuel": r["fuel"],
                # occto = OCCTO の分類そのまま / matched = 照合先から推定
                "fuel_source": r.get("fuel_source"),
                "kwh": r["kwh_sample_day"],
                # ★ 設備容量（MW）
                "capacity_mw": r.get("capacity_mw"),
                # ⚠️ `cf` は**当日平均**の設備利用率（累計 ÷ 容量×24h）。
                #    扇形には使わない —— 昼だけ全開の機組が 40% に見えてしまう。
                "cf": (round(min((r["kwh_sample_day"] or 0) / 1000.0
                                 / (r["capacity_mw"] * 24), 1.0), 4)
                       if r.get("capacity_mw") else None),
                # ★ 扇形が表すのは**その時刻の出力 ÷ 容量**。
                #   前端はスライダーのコマで `slots_mw` から引き直す。
                #   ここの `mw_now` / `load` は最新コマの値（初期表示・凡例用）。
                "mw_now": latest,
                "load": (round(min(latest / cap, 1.0), 4)
                         if cap and latest is not None else None),
                # ★ 30 分コマ。潮流と同じ時間軸（`flow_stamps.json` と一対一）
                "slots_mw": slots,
                # ★ 扇形用。`slots_mw` と同じ長さの**文字列**（1 コマ 1 文字）。
                #   layout プロパティは配列を扱えないので、こちらを使う（上の注記）
                "fsteps": fan_steps(slots, cap),
                # GEM_operating = 現役のみ / AJG_installed_ever = 廃炉分を含む
                "capacity_source": r.get("capacity_source"),
                "data_quality": "measured",          # OCCTO 逐机组实测
                "coord_source": r["coord_source"], "match_method": r["match_method"],
            },
        })
    # 実測が入っている最後のコマ。1 機でも値があればそのコマは「ある」とみなす
    last = max((i for f in feats
                for i, v in enumerate(f["properties"]["slots_mw"] or [])
                if v is not None), default=-1)
    if stamps:
        log.info("機組の時系列を時間軸に接続: %d / %d 機組（実測は %d コマ目まで）",
                 n_hit, len(feats), last)
    return write("plants.geojson", feats), last


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


def write_manifest(built: list[str]) -> None:
    """どのファイルがどちらの層で、いつ更新されたかを残す。

    配信スクリプトはこれを見て**キャッシュ制御を層ごとに変える**
    （static は長期キャッシュ、dynamic は短期）。
    ⚠️ 層の定義は `spec.LAYER_FILES` が唯一の出所。ここで並べ直さない。
    """
    d = out_dir()
    man: dict = {"built": built}
    for layer, names in spec.LAYER_FILES.items():
        man[layer] = [
            {"file": n, "bytes": (d / n).stat().st_size,
             "mtime": int((d / n).stat().st_mtime)}
            for n in names if (d / n).is_file()
        ]
    (d / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="structured → 前端用 GeoJSON")
    # 選択肢も spec から。ここで並べ直すと定義が 2 つになる
    ap.add_argument("--only", choices=spec.SCOPES, default="all",
                    help="dynamic は 30 分更新用。static を作り直さないぶん速い")
    args = ap.parse_args()
    fetch.setup_logging()

    n_line = n_sub = 0
    b_line = b_sub = b_site = b_flow = b_plant = 0
    built: list[str] = []

    if args.only in ("all", "static"):
        n_line, b_line, n_sub, b_sub = build_grid()
        b_site = build_sites()
        built += spec.files_for("static")
    if args.only in ("all", "dynamic"):
        # ★ 潮流が決めた時間軸を機組にも渡す。スライダーは 1 本で両方を動かす
        b_flow, stamps = build_flow()
        b_plant, plant_last = build_plants(stamps)
        # ★ 出すのは事実（機組がどこまであるか）。既定コマの判断は前端
        write_stamps(stamps, plant_last)
        built += spec.files_for("dynamic")

    write_manifest(built)
    total = b_line + b_sub + b_flow + b_plant + b_site
    log.info("今回書いた %d ファイル / %.1f MB（未压缩）", len(built), total / 1024 / 1024)
    report.emit(
        path=str(out_dir()), bytes=total, only=args.only, built=built,
        size_human=report.human_bytes(total),
        grid_lines=n_line, substations=n_sub,
        simplified=False,          # ⛔ 硬约束：几何绝不简化
        note="GeoJSON 未压缩；CDN 侧 gzip/br 后约为 1/3",
    )


if __name__ == "__main__":
    main()
