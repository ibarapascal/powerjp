"""从 AJG 原始线段自建送电网图。

# ❌❌❌ 这个方案已验证走不通，不要用。保留作为否定结论的证据。 ❌❌❌

**结论：继续用 AJG 的 `docs/data/built/all.json`，别自己建。**

## 当初的（错误）假设

以为「AJG 缝合把 40,077 条原始线段丢成 19,031 条边，丢了 53%」。

**这个假设是错的**：那是**合并**不是丢弃 —— AJG 把共线的多个 OSM 线段并成一条边，
`lines_all.geojson` 恰好也是 19,031，与 built 的边数完全相等。

另一个促使我自建的理由「AJG 缺 西双葉/旭川嵐山/双葉 開閉所」**也是错的** ——
那是我自己的 bug：OSM 的 `name` 有时是罗马字，我写 `name or name:ja`
取到了英文名（见 LESSONS.md #16）。

## 实测结果：更差

| | AJG built | 本方案（sub-snap 300m） |
|---|---:|---:|
| 主连通分量占比 | **65.9%** | 56.7% |
| 连通分量数 | 1,096 | 2,545 |

AJG 的缝合（`n_stitch: 400`）比朴素端点吸附高明得多。

## 留着它干什么

1. 防止后人重走这条路（见 LESSONS.md #17）
2. 里面的**变电所几何加载 + 空间网格索引**逻辑本身可复用

原理仍记录如下，仅供参考。

## 建图方式

1. **节点 = 吸附到网格的坐标**。输电线在 OSM 里是一段段的 LineString，
   相邻段的端点坐标常有微小差异，按网格取整才能接上。
2. **变电所吸附**：线端点落在变电所附近（`SUB_SNAP_M`）就并到该变电所节点上 ——
   这是「线连到站」的关键，否则站和线是两套互不相连的东西。
3. 边保留原始 `path` 几何、电压、名称。

`SNAP_M` 是唯一的调参旋钮：太小接不上，太大会把不相干的线connect在一起
（后者更危险，会造出物理上不存在的通路）。

用法：
    python -m pipeline.structured.rawgraph                 # 建图并报告连通性
    python -m pipeline.structured.rawgraph --snap 30
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict, deque
from pathlib import Path

from pipeline.core import fetch, paths
from pipeline.structured.flow_match_probe import norm_station

log = logging.getLogger("powerjp.rawgraph")

AJG_GLOB = "v*"          # ⚠️ 版を固定しない（CLAUDE.md「上流の版を書かない」）
SNAP_M = 25.0          # 线段端点互相吸附的半径（米）
SUB_SNAP_M = 400.0     # 线端点吸附到变电所的半径（米）
REGIONS = ["hokkaido", "tohoku", "tokyo", "chubu", "hokuriku",
           "kansai", "chugoku", "shikoku", "kyushu", "okinawa"]


def m_per_deg(lat: float) -> tuple[float, float]:
    """在给定纬度下，1 度纬/经各是多少米。日本纬度范围内够用。"""
    return 111_132.0, 111_320.0 * math.cos(math.radians(lat))


def haversine_m(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * \
        math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(math.sqrt(h))


def build(snap_m: float = SNAP_M, sub_snap_m: float = SUB_SNAP_M):
    base = paths.latest_version("ajg", AJG_GLOB) / "source"
    src = [p for p in base.iterdir() if p.is_dir()][0]
    data = src / "data"

    # ---- 变电所：先建空间索引（网格哈希），供线端点吸附 ----
    subs: list[dict] = []
    for region in REGIONS:
        f = data / f"{region}_substations.geojson"
        if not f.is_file():
            continue
        for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
            p = ft.get("properties", {}) or {}
            # ⚠️ OSM 的 `name` 有时是**罗马字**（"Nishifutaba Switching Station"），
            #    日文在 `name:ja`。写成 `name or name:ja` 会拿到英文名，
            #    于是「西双葉開閉所」永远匹配不上 —— 必须两个都收。
            names = [str(p[k]) for k in ("name:ja", "name", "name:en") if p.get(k)]
            if not names:
                continue
            g = ft.get("geometry") or {}
            pt = _centroid(g)
            if pt:
                norms = {norm_station(n) for n in names}
                norms.discard("")
                subs.append({"name": names[0], "norm": names[0] and norm_station(names[0]),
                             "norms": norms,
                             "lat": pt[0], "lon": pt[1], "region": region,
                             "kv": p.get("voltage")})
    log.info("变电所 %d 个（带几何）", len(subs))

    cell = sub_snap_m / 111_132.0          # 用纬度换算的网格边长（度）
    sub_grid: dict[tuple, list[int]] = defaultdict(list)
    for i, s in enumerate(subs):
        sub_grid[(int(s["lat"] / cell), int(s["lon"] / cell))].append(i)

    def nearest_sub(lat: float, lon: float) -> int | None:
        gi, gj = int(lat / cell), int(lon / cell)
        best, bestd = None, sub_snap_m
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in sub_grid.get((gi + di, gj + dj), ()):
                    d = haversine_m((lat, lon), (subs[i]["lat"], subs[i]["lon"]))
                    if d < bestd:
                        best, bestd = i, d
        return best

    # ---- 节点键：吸附到网格；落在变电所附近的统一并到变电所 ----
    # ⚠️ 变电所节点必须**预先全部建好**。早先是「线端点吸附到它时才建」，
    #    结果 6,962 个变电所只有 4,404 进图，附近没有线端点的（如 西双葉開閉所）
    #    直接从图里消失，路径求解报「端点不在图中」，误以为是数据缺失。
    node_of_sub: dict[int, str] = {i: f"S{i}" for i in range(len(subs))}
    nodes: dict[str, dict] = {
        f"S{i}": {"lat": s_["lat"], "lon": s_["lon"], "sub": True,
                  "name": s_["name"], "norm": s_["norm"], "region": s_["region"]}
        for i, s_ in enumerate(subs)
    }

    def node_key(lat: float, lon: float, region: str) -> str:
        si = nearest_sub(lat, lon)
        if si is not None:
            return node_of_sub[si]
        dlat, dlon = m_per_deg(lat)
        k = f"J{round(lat * dlat / snap_m)}:{round(lon * dlon / snap_m)}"
        if k not in nodes:
            nodes[k] = {"lat": lat, "lon": lon, "sub": False,
                        "name": "", "norm": "", "region": region}
        return k

    adj: dict[str, list[tuple]] = defaultdict(list)
    n_seg = 0
    n_collapsed = [0]
    n_feat = 0
    for region in REGIONS:
        f = data / f"{region}_lines.geojson"
        if not f.is_file():
            continue
        for ft in json.loads(f.read_text(encoding="utf-8")).get("features", []):
            g = ft.get("geometry") or {}
            n_feat += 1
            for coords in _linestrings(g):
                if len(coords) < 2:
                    continue
                pts = [(c[1], c[0]) for c in coords]          # GeoJSON 是 [lon,lat]
                a = node_key(pts[0][0], pts[0][1], region)
                b = node_key(pts[-1][0], pts[-1][1], region)
                if a == b:
                    # 两端吸到同一节点（多见于变电所内部短线）——丢弃但计数，
                    # 这个数字能反映吸附半径是否过大
                    n_collapsed[0] += 1
                    continue
                p = ft.get("properties", {}) or {}
                kv = _kv(p.get("voltage"))
                w = sum(haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) / 1000
                nm = p.get("name:ja") or p.get("name") or ""
                adj[a].append((b, w or 0.001, kv, pts, nm))
                adj[b].append((a, w or 0.001, kv, list(reversed(pts)), nm))
                n_seg += 1
    log.info("要素 %d → 线段 %d（自环丢弃 %d）→ 节点 %d / 有邻接 %d",
             n_feat, n_seg, n_collapsed[0], len(nodes), len(adj))

    by_name: dict[str, list[str]] = defaultdict(list)
    for i, s_ in enumerate(subs):
        for nn in s_.get("norms") or ():
            by_name[nn].append(f"S{i}")
    return nodes, by_name, adj


def _centroid(geom: dict):
    t = geom.get("type")
    c = geom.get("coordinates")
    if not c:
        return None
    if t == "Point":
        return (c[1], c[0])
    if t == "Polygon":
        ring = c[0]
        return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))
    if t == "MultiPolygon":
        ring = c[0][0]
        return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))
    return None


def _linestrings(geom: dict):
    t = geom.get("type")
    c = geom.get("coordinates") or []
    if t == "LineString":
        yield c
    elif t == "MultiLineString":
        yield from c


def _kv(v) -> float:
    if not v:
        return 0.0
    s = str(v).split(";")[0].strip()
    try:
        n = float(s)
    except ValueError:
        return 0.0
    return n / 1000 if n > 1000 else n          # OSM 用伏特，图里用 kV


def components(adj) -> list[int]:
    seen, sizes = set(), []
    for start in adj:
        if start in seen:
            continue
        q, n = deque([start]), 0
        seen.add(start)
        while q:
            cur = q.popleft(); n += 1
            for nxt, *_ in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt); q.append(nxt)
        sizes.append(n)
    sizes.sort(reverse=True)
    return sizes


def main() -> None:
    ap = argparse.ArgumentParser(description="从原始线段自建送电网图")
    ap.add_argument("--snap", type=float, default=SNAP_M)
    ap.add_argument("--sub-snap", type=float, default=SUB_SNAP_M)
    args = ap.parse_args()
    fetch.setup_logging()

    nodes, by_name, adj = build(args.snap, args.sub_snap)
    sizes = components(adj)
    total = sum(sizes)
    print()
    print("═" * 60)
    print(f"  吸附半径 线段 {args.snap:.0f} m / 变电所 {args.sub_snap:.0f} m")
    print(f"  节点 {len(nodes)}  有邻接 {len(adj)}  变电所归一名 {len(by_name)}")
    print(f"  连通分量 {len(sizes)}  最大 {sizes[0]}（占 {sizes[0]/total*100:.1f}%）")
    print(f"  前 5 大分量：{sizes[:5]}")
    print("═" * 60)
    print("\n  对比 AJG built/all.json：节点 17,333 / 边 19,031 / 主分量占 65.9%")
    for probe in ["西双葉", "旭川嵐山", "双葉", "大野", "西滝川", "名城", "岐阜"]:
        print(f"   {probe:8} {'✅' if probe in by_name else '❌'}")


if __name__ == "__main__":
    main()
