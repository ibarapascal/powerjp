"""潮流 → **真实几何** 的图路径求解（probe）。

## 为什么要这个

靠名字匹配（`flow_match_probe`）只能给 63% 的线拿到真实几何，剩下的只能画直线示意。
原因是 OCCTO 的干线名（常磐南幹線・宮城幹線・五頭幹線…）在 OSM 里**根本没有对应名字**。

**换个思路**：潮流给的是「起点変電所 → 终点変電所」。AJG 的 `docs/data/built/all.json`
是一张完整的图 —— 17,333 个节点（带坐标）、19,031 条边（**带完整 `path` 几何**）。
那就不匹配名字，直接**在图上求两端点之间的路径**，把沿途边的 `path` 拼起来 ——
这就是那条线的真实走线。

名字对不上无所谓，物理路径是客观的。

## 约束

- **电压过滤**：500kV 的潮流不该绕经 66kV 线路。按潮流 CSV 的 `電圧` 限制可走的边。
- **只走主连通分量**：孤岛节点上的路径没有意义。
- 只有 14,048/19,031 条边带 `path` 几何（74%），缺几何的边用两端点直连补。

用法：
    python -m pipeline.structured.flow_route_probe
    python -m pipeline.structured.flow_route_probe --area 3
    python -m pipeline.structured.flow_route_probe --kv-tolerance loose
"""

from __future__ import annotations

import argparse
import csv
import heapq
import io
import json
import logging
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.core import fetch, paths, report
from pipeline.structured.flow_match_probe import norm_station

log = logging.getLogger("powerjp.flow_route")

AJG_GLOB = "v*"          # ⚠️ 版を固定しない（LESSONS #34）
# 潮流 CSV 的電圧字段是「500」「275」这种字符串；图里的 kv 是浮点
# 潮流的 対象エリア ↔ 图节点的 region。
# ⚠️ 不按区域约束会出大错：「大平」在九州和北海道都有，全国范围找会匹配到
#    北海道那个，于是画出一条 1,358 km 横跨日本的假线（实测踩过）。
AREA_TO_REGION = {
    "北海道": "hokkaido", "東北": "tohoku", "東京": "tokyo", "中部": "chubu",
    "北陸": "hokuriku", "関西": "kansai", "中国": "chugoku", "四国": "shikoku",
    "九州": "kyushu", "沖縄": "okinawa",
}

KV_NEIGHBOURS = {500.0: {500.0}, 275.0: {275.0, 220.0}, 220.0: {220.0, 275.0},
                 187.0: {187.0}, 154.0: {154.0}, 132.0: {132.0}, 110.0: {110.0},
                 77.0: {77.0}, 66.0: {66.0}}


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """两点球面距离（km）。边权用它，才能求出「最短的真实走线」。"""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * \
        math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def load_graph():
    base = paths.latest_version("ajg", AJG_GLOB) / "source"
    roots = [p for p in base.iterdir() if p.is_dir()]
    d = json.loads((roots[0] / "docs" / "data" / "built" / "all.json")
                   .read_text(encoding="utf-8"))

    def key(lat, lon) -> str:
        return f"{float(lat):.5f},{float(lon):.5f}"

    # ---- 先收原始变电所的**全部名称变体** ----
    # ⚠️ OSM 的 `name` 有时是罗马字（"Nishifutaba Switching Station"），日文在 `name:ja`。
    #    built/all.json 的节点名只有一个字段，可能就是那个英文名 ——
    #    于是「西双葉開閉所」在图里搜不到，被误判成「AJG 缺这个站」。
    #    对策：按坐标把原始文件的所有名称变体贴回图节点。
    raw_subs: list[tuple[float, float, set[str]]] = []
    # ⚠️ **发电所也是潮流端点**。潮流 CSV 里大量端点是「金武火力変電所」
    #    「具志川火力変電所」「吉の浦火力変電所」这种厂内变电所，AJG 侧叫
    #    「金武火力発電所」—— norm_station 两边都归一成「金武火力」本该对上，
    #    但早先索引只收 substations，发电所根本没进来。实测沖縄一个区域
    #    就因此卡住 37 条线次（该区 31 条线全部失败）。
    #    只收 plants_utility + generators 这两个**事业用**图层 ——
    #    全量 {region}_plants 有 19,138 个，绝大多数是小太阳能，
    #    归一后名字很短，进索引只会制造撞名误配（见 PLANT_MATCHING「静默错误 A」）。
    src_files = sorted((roots[0] / "data").glob("*_substations*.geojson"))
    src_files += [roots[0] / "docs" / "data" / "plants_utility.geojson",
                  roots[0] / "docs" / "data" / "generators.geojson"]
    for f in src_files:
        if not f.is_file():
            continue
        for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
            pr = ft.get("properties", {}) or {}
            names = {str(pr[k]) for k in ("name:ja", "name", "name:en") if pr.get(k)}
            if not names:
                continue
            g = ft.get("geometry") or {}
            c = g.get("coordinates")
            if not c:
                continue
            t = g.get("type")
            if t == "Point":
                pt = (c[1], c[0])
            elif t == "Polygon":
                ring = c[0]
                pt = (sum(x[1] for x in ring) / len(ring), sum(x[0] for x in ring) / len(ring))
            elif t == "MultiPolygon":
                ring = c[0][0]
                pt = (sum(x[1] for x in ring) / len(ring), sum(x[0] for x in ring) / len(ring))
            else:
                continue
            norms = {norm_station(n) for n in names}
            norms.discard("")
            if norms:
                # 発電所は厂区が大きく、引出線の接入点が中心から数 km 離れることがある。
                # ⚠️ 変電所と同じ 0.6 km では 吉の浦火力/金武火力 のように
                #    「AJG にデータはあるのに貼り付かない」が起きる（沖縄は図の節点密度も低い）。
                raw_subs.append((pt[0], pt[1], norms, "plant" if "plants" in f.name
                                 or "generators" in f.name else "sub"))
    CELL = 0.01                                     # 约 1.1 km 的网格
    rs_grid: dict[tuple, list[int]] = defaultdict(list)
    for i, (la, lo, _, _) in enumerate(raw_subs):
        rs_grid[(int(la / CELL), int(lo / CELL))].append(i)

    # 変電所は敷地が小さいので狭く、発電所は厂区が大きいので広く。
    # 発電所を 0.6 km のままにすると AJG にある 吉の浦火力/金武火力 が貼り付かない。
    RADIUS = {"sub": 0.6, "plant": 2.5}
    RING = 3                                        # 2.5 km / 1.1 km ≈ 2.3 → 3 格

    def names_near(lat: float, lon: float) -> set[str]:
        out: set[str] = set()
        gi, gj = int(lat / CELL), int(lon / CELL)
        for di in range(-RING, RING + 1):
            for dj in range(-RING, RING + 1):
                for i in rs_grid.get((gi + di, gj + dj), ()):
                    la, lo, nms, kind = raw_subs[i]
                    if haversine((lat, lon), (la, lo)) <= RADIUS[kind]:
                        out |= nms
        return out

    nodes: dict[str, dict] = {}
    by_name: dict[str, list[str]] = defaultdict(list)
    for n in d["nodes"]:
        k = key(n["lat"], n["lon"])
        nodes[k] = {"lat": float(n["lat"]), "lon": float(n["lon"]),
                    "kv": float(n.get("kv") or 0), "main": str(n.get("main")) == "1",
                    "sub": str(n.get("sub")) == "1", "name": n.get("name", "")}
        nodes[k]["region"] = n.get("region", "")
        # ⚠️ 对**所有**节点贴附邻近设施名，不只 sub==1 的 ——
        #    发电所引出线接入的图节点常常是 junction（sub=0），
        #    只给变电所建索引的话，发电所端点永远找不到。
        variants = {norm_station(n.get("name", ""))} if str(n.get("sub")) == "1" else set()
        variants |= names_near(nodes[k]["lat"], nodes[k]["lon"])
        variants.discard("")
        for nn in variants:
            by_name[nn].append(k)

    adj: dict[str, list[tuple[str, float, float, list]]] = defaultdict(list)
    n_geom = 0
    for e in d["edges"]:
        def as_pair(v):
            # built/all.json 里 a/b/path 有时是 list，有时是它的字符串表示
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except ValueError:
                    return None
            return v
        a, b = as_pair(e.get("a")), as_pair(e.get("b"))
        if not (isinstance(a, list) and isinstance(b, list) and len(a) >= 2 and len(b) >= 2):
            continue
        ka, kb = key(a[0], a[1]), key(b[0], b[1])
        kv = float(e.get("kv") or 0)
        path = as_pair(e.get("path"))
        if not (isinstance(path, list) and len(path) >= 2):
            path = None
        if path:
            n_geom += 1
            has_geom = True
            w = sum(haversine(tuple(path[i]), tuple(path[i + 1]))
                    for i in range(len(path) - 1)) or 0.001
        else:
            # ⚠️ 幾何が無い辺は両端の直線で代用している。
            #    経路がここを通ると**実在しない直線**が地図に出る。
            #    段ごとに印を付けて、publish/前端で除外できるようにする。
            has_geom = False
            path = [a, b]
            w = haversine(tuple(a), tuple(b)) or 0.001
        adj[ka].append((kb, w, kv, path, has_geom))
        adj[kb].append((ka, w, kv, list(reversed(path)), has_geom))
    log.info("图：节点 %d / 邻接 %d / 带几何的边 %d / 可检索端点名 %d",
             len(nodes), len(adj), n_geom, len(by_name))
    return nodes, by_name, adj


# 路径长度不得超过两端直线距离的这个倍数。
# 超过说明 Dijkstra 绕了远路（实测出现过 1,704 km 的路径 —— 日本南北才约 3,000 km），
# 那种线画到地图上是假的，宁可判失败也不能画。
MAX_DETOUR_RATIO = 2.0
MIN_DIRECT_KM = 2.0        # 两端很近时比值没意义，给个下限

# 地内送電線的两端直线距离上限。用它取代「区域必须一致」的硬约束 ——
# ⚠️ AJG 的 region 是**地理地方**，OCCTO 的 対象エリア 是**电力区域**，两者不等价：
#    いわき（福島県）地理属 tohoku，供电属 東京電力；西播（兵庫県）被标成 chugoku。
#    按区域硬拒会把 新いわき・南いわき・新福島・西播 这些**真实存在的站**全部误杀。
#    真正防住「大平 1,358 km 跨岛误配」的是距离，不是区域。
MAX_ENDPOINT_KM = 300.0


def seg_coords(segs) -> list:
    """分段 → 拼接后的坐标序列。"""
    out: list = []
    for s in segs:
        out.extend(s["coords"])
    return out


def route(adj, start: str, goal: str, allowed_kv: set[float] | None,
          max_hops: int = 400) -> list | None:
    """Dijkstra，边权 = 实际线路长度。

    ★ 返回**分段**（`[{"kv": …, "coords": […]}, …]`）而不是拼好的坐标串。

    ⚠️ 一条潮流线的路径会经过**不同电压的边**（電圧を放宽して求解した 18 本は特に）。
    坐标串に潰すとその情報が消え、前端では「線路全体を 1 つの電圧」として
    描くしかなくなる。すると下の送電線と幅が合わず、太さの違う部分が
    はみ出して枠のように見える。段ごとの kv を保持すること。
    """
    if start == goal:
        return None
    dist = {start: 0.0}
    prev: dict[str, tuple[str, list]] = {}
    pq = [(0.0, start, 0)]
    seen = set()
    while pq:
        dcur, cur, hops = heapq.heappop(pq)
        if cur in seen:
            continue
        seen.add(cur)
        if cur == goal:
            break
        if hops >= max_hops:
            continue
        for nxt, w, kv, path, has_geom in adj.get(cur, []):
            if allowed_kv and kv and kv not in allowed_kv:
                continue
            nd = dcur + w
            if nd < dist.get(nxt, float("inf")):
                dist[nxt] = nd
                prev[nxt] = (cur, path, kv, has_geom)
                heapq.heappush(pq, (nd, nxt, hops + 1))
    if goal not in prev and goal != start:
        return None
    segs: list = []
    cur = goal
    while cur != start:
        p, path, kv, has_geom = prev[cur]
        segs.insert(0, {"kv": kv, "coords": list(path), "has_geom": has_geom})
        cur = p
    return segs


def load_flow(area: int | None) -> list[dict]:
    root = paths.raw("occto_flow")
    files = sorted(root.rglob("chinai_kikan_jisseki_*.csv"))
    if area:
        files = [f for f in files if f.stem.endswith(f"_{area:02d}")]
    latest: dict[str, Path] = {}
    for f in files:
        latest[f.stem[-2:]] = f
    rows = []
    for _, f in sorted(latest.items()):
        text = f.read_bytes().decode("shift_jis", errors="replace")
        rows.extend(csv.DictReader(io.StringIO(text)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="潮流 → 真实几何（图路径求解）")
    ap.add_argument("--area", type=int)
    ap.add_argument("--kv-tolerance", choices=["strict", "loose", "off"], default="strict")
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--dump", help="把求出的路径几何写到 JSON（line_xref 的雏形，S3）")
    args = ap.parse_args()
    fetch.setup_logging()

    nodes, by_name, adj = load_graph()
    rows = load_flow(args.area)

    seen = set()
    stat = Counter()
    lengths: list[float] = []
    longest: list[tuple] = []
    failures: list[str] = []
    dumped: list[dict] = []
    for r in rows:
        ln = (r.get("送電線名") or "").strip()
        area_nm = (r.get("対象エリア") or "").strip()
        if (area_nm, ln) in seen:
            continue
        seen.add((area_nm, ln))

        parts = [p.strip() for p in re.split(r"[→⇒>]", r.get("潮流方向(正方向)") or "") if p.strip()]
        if len(parts) < 2:
            stat["方向字段异常"] += 1
            continue
        want_region = AREA_TO_REGION.get(area_nm)

        def pick(nm: str) -> list[str]:
            """同名候选：本区域优先，但**不排除**其他区域。

            排除跨区域候选是错的 —— AJG 的 region 是地理地方，OCCTO 的
            対象エリア 是电力区域（いわき 地理属東北、供电属東京）。
            误配靠下面的 `MAX_ENDPOINT_KM` + 绕行比来防，不靠区域。
            """
            cands = by_name.get(norm_station(nm)) or []
            same = [c for c in cands if nodes[c].get("region") == want_region]
            other = [c for c in cands if nodes[c].get("region") != want_region]
            return same + other          # 本区域排前面，配对时优先取到

        a_key = pick(parts[0])
        b_key = pick(parts[1])
        if not a_key or not b_key:
            stat["端点不在图中"] += 1
            failures.append(f"{area_nm} {ln}  ({parts[0]} → {parts[1]})")
            continue

        kv_raw = (r.get("電圧") or "").strip()
        allowed = None
        if args.kv_tolerance != "off":
            try:
                kv = float(kv_raw)
                allowed = KV_NEIGHBOURS.get(kv, {kv}) if args.kv_tolerance == "loose" else {kv}
            except ValueError:
                allowed = None

        def path_km(c):
            cs = seg_coords(c)
            return sum(haversine(tuple(cs[i]), tuple(cs[i + 1])) for i in range(len(cs) - 1))

        def acceptable(c, s_key, g_key):
            """两道守卫一起查：**绝对距离** + 绕行比。

            ⚠️ 早先这里只查绕行比，而「放宽电压」的回退分支只调用了本函数 ——
            于是 1,358 km 的跨岛误配（绕行比才 1.26x）从那个口子漏了进来。
            两道都必须在同一个地方查，不能分散。
            """
            if not c:
                return False
            direct = haversine((nodes[s_key]["lat"], nodes[s_key]["lon"]),
                               (nodes[g_key]["lat"], nodes[g_key]["lon"]))
            if direct > MAX_ENDPOINT_KM:
                return False
            return path_km(c) <= max(direct, MIN_DIRECT_KM) * MAX_DETOUR_RATIO

        best = None
        rejected = 0
        far = 0
        for s in a_key[:4]:
            for g in b_key[:4]:
                # 端点距离守卫：地内线不可能横跨 300 km 以上
                if haversine((nodes[s]["lat"], nodes[s]["lon"]),
                             (nodes[g]["lat"], nodes[g]["lon"])) > MAX_ENDPOINT_KM:
                    far += 1
                    continue
                c = route(adj, s, g, allowed)
                if not c:
                    continue
                if not acceptable(c, s, g):
                    rejected += 1
                    continue
                if best is None or path_km(c) < path_km(best):
                    best = c
        if best is None and rejected:
            stat["绕行比超限被拒"] += 1
        elif best is None and far and not rejected:
            stat["端点距离超 300km 被拒"] += 1
        if best is None and allowed:                       # 电压约束下不通，放宽再试
            # ⚠️ **完全放開（allowed_kv=None）にしてはいけない。**
            #    実測：275kV の高根中信線が 66/77kV の配電線を 62 区間ぶん
            #    通る経路になった。川越火力線 43 区間、千葉葛南線 22 区間も同様。
            #    幹線が配電線を延々と辿る経路は物理的にあり得ず、
            #    「両端が合っていて長さが妥当」なだけの偽の経路になる。
            #    緩めるのは**隣接する電圧階級まで**（KV_NEIGHBOURS）。
            relaxed = KV_NEIGHBOURS.get(next(iter(allowed)), set()) - allowed
            if relaxed:
                for s in a_key[:2]:
                    for g in b_key[:2]:
                        c = route(adj, s, g, allowed | relaxed)
                        if c and acceptable(c, s, g) and (best is None or path_km(c) < path_km(best)):
                            best = c
                if best:
                    stat["隣接電圧まで緩めて成功"] += 1
            if best is None:
                stat["電圧制約下で経路なし"] += 1
        if best:
            stat["✅ 求出真实路径"] += 1
            L = path_km(best)
            direct = haversine((nodes[a_key[0]]["lat"], nodes[a_key[0]]["lon"]),
                               (nodes[b_key[0]]["lat"], nodes[b_key[0]]["lon"]))
            lengths.append(L)
            longest.append((L, direct, f"{area_nm} {ln} ({parts[0]}→{parts[1]})"))
            dumped.append({"area": area_nm, "line": ln, "kv": kv_raw,
                           "from": parts[0], "to": parts[1], "km": round(L, 3),
                           # ⚠️ 前端必须能区分真走线和示意线
                           "geometry_source": "graph_route",
                           # 段ごとに実際の電圧を持つ。publish 側でこの単位に分けて出す
                           "segments": best,
                           "path": seg_coords(best)})
        else:
            stat["图中不连通"] += 1
            failures.append(f"{area_nm} {ln}  ({parts[0]} → {parts[1]}) 不连通")

    n = len(seen)
    ok = stat["✅ 求出真实路径"]
    print()
    print("═" * 66)
    print(f"  潮流的「区域 × 送電線」条目      {n:>5}")
    for k, v in stat.most_common():
        print(f"  {k:26} {v:>5}  ({v/n*100:.1f}%)")
    print("  " + "─" * 62)
    print(f"  ★ 拿到真实几何                  {ok:>5}  ({ok/n*100:.1f}%)")
    if lengths:
        lengths.sort()
        print(f"     路径长度中位数 {lengths[len(lengths)//2]:.1f} km / "
              f"最长 {lengths[-1]:.1f} km")
    print("═" * 66)
    longest.sort(reverse=True)
    print(f"\n最长的 8 条路径（检查是否绕远）：")
    for L, direct, label in longest[:8]:
        ratio = L / direct if direct > 0.01 else float("inf")
        flag = "  ⚠️ 可疑" if ratio > 1.8 or L > 300 else ""
        print(f"   {L:8.1f} km  直线 {direct:7.1f} km  比 {ratio:5.2f}x  {label[:52]}{flag}")
    print(f"\n失败样本（前 {args.show}）：")
    for f in failures[:args.show]:
        print("  ", f)

    report.emit(
        flow_lines_total=n, routed=ok,
        routed_pct=round(ok / n * 100, 2) if n else 0.0,
        median_km=round(lengths[len(lengths) // 2], 1) if lengths else None,
        max_km=round(lengths[-1], 1) if lengths else None,
        endpoint_missing=stat["端点不在图中"],
        disconnected=stat["图中不连通"],
        geometry_source="graph_route",
        output=args.dump,
    )

    if args.dump:
        Path(args.dump).write_text(json.dumps(dumped, ensure_ascii=False), encoding="utf-8")
        log.info("写出 %d 条路径 → %s", len(dumped), args.dump)


if __name__ == "__main__":
    main()
