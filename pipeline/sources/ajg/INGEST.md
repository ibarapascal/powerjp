# All-Japan-Grid — 送电网几何

> 每个数据源的 INGEST.md 结构统一：概要 / 获取 / 落盘 / 许可 / 已知坑。
> 许可三字段在**首次取数当时**填写，不留到以后统一整理。

## 概要

OSM 日本电力图层的社区整理成果，是**唯一同时具备「线 + 点 + 坐标 + 格式友好」的源**。官方矢量坐标不存在，各社系統図只有 PDF 示意图。

| 项 | 值 |
|---|---|
| 内容 | 送电线廊道 / 变电所 / 发电所，全 10 区域 |
| 规模 | 约 40,077 线、6,962 变电所、19,138 发电所「以调研时点为准，取数后以实际为准」 |
| 坐标系 | WGS84 |
| 输出格式 | GeoJSON / CIM/CGMES / MATPOWER / pandapower |
| 更新 | 活跃维护 |

## 获取

**类型：`auto`** —— 无需凭据。

`pipeline/sources/ajg/ingest.py` 取**两份**，缺一不可：

| 取什么 | 来源 | 为什么需要 |
|---|---|---|
| **源码 tarball** | `archive/refs/tags/{tag}.tar.gz`（90MB） | **主源几何在这里** —— repo 的 `data/` 下 30 个「区域 × {lines,substations,plants}」GeoJSON。release bundle 里没有 |
| **release bundle** | `{tag}/…-full.zip`（24MB） | datapackage 声明的 9 个配信资源 + `dist/ybus`、`dist/matpower_national`、`dist/cim_level2`（建模用）+ **`docs/data/built/`（拓扑正典）** |
| **MANIFEST.sha256** | 同上 | bundle 的**逐文件**摘要（237 个文件），不是 zip 自身的摘要 |

⚠️ **bundle 和 source 的 `docs/data/` 内容不一样**：bundle 只有 4 个 geojson，
**source tarball 有 11 个** —— 多出 `generators` / `plants_utility` / `plants_ipp` /
`lines_275kv` / `lines_154kv` / `subs_275kv` / `subs_154kv`。
最有价值的 `generators.geojson` 就在 source 侧（见「已知坑」）。

版本策略：**既定追随 GitHub Releases 的 latest**（取不到就抛异常，不静默回退旧版；
理由见 CLAUDE.md「ingest も版を書かない」）。只在需要再现性时用 `--tag` 固定。

## 落盘

```
storage/raw/ajg/{tag}/
├── source-{tag}.tar.gz          原始归档
├── bundle-{tag}-full.zip
├── bundle-{tag}-full.MANIFEST.sha256
├── source/All-Japan-Grid-{ver}/ 解压后的主源（data/ 在这里）
└── bundle/…-full/               解压后的 bundle（docs/data/built 在这里）
```

约 500MB/版本。

## 实测数字

| | v1.6.0（2026-08-01） | v1.7.0（2026-08-27） |
|---|---:|---:|
| 送电线 lines | 40,077 | **40,087** |
| 变电所 substations | 6,962 | **7,240** |
| 发电所 plants | 19,138 | **19,138** |
| bundle sha256 逐文件 | 237/237 通过 | **253/253 通过** |

v1.7.0 的主题是「公表資料での正本化」：連系線/変換所几何修正（合成直線 tie 除去、
阿南紀北 DC 実ルート化）、様式5 接続 89 件补拓扑、GEM 容量补充 +194 件/22.5GW、
`interconnections.yaml` 改为**方向别**运用容量（关门 順 850 / 逆 2,850 MW）。
方向别容量对本项目的负荷率概算（LINE_CAPACITY_MW）是潜在改善点，尚未接入。

另有 supplement 补充几何（不计入上表）：`tokyo_plants_supplement` 4,608、`hokuriku_lines_supplement` 321、`kansai_lines_supplement` 317、`tokyo_lines_supplement` 218、`tokyo_substations_supplement` 77。

## 许可

| 字段 | 值 |
|---|---|
| `source_url` | https://github.com/lutelute/All-Japan-Grid |
| `licence` | 数据 ODbL / 代码 MIT |
| `redistributable` | **yes** —— 需署名 + 衍生数据库同样以 ODbL 开放 |

ODbL 的 share-alike 会传染到由它派生的发布产物。因此各图层保持可分离，不让其污染无关输出。

## 已知坑

### ★ `lines_all` 的 19,031 vs 40,077 —— 不是 bug，是两种东西

首次取数时发现配信层 `docs/data/lines_all.geojson` 只有 **19,031** features，与一次源的 40,077 差一半。查清楚了：

- **40,077** = `data/{region}_lines.geojson` 的原始 OSM 线段数（一次源）
- **19,031** = 缝合成拓扑后的**边数**，与 `docs/data/built/index.json` 里 `all.n_edges` **完全相等**

即：配信层是把原始线段接成网络之后的产物，不是原始几何。**两者用途不同，都要留**。可视化几何用一次源，拓扑分析用 built。

### ★ 接続不完整的量化值（来自 `docs/data/built/index.json`）

不要再引用「接続召回 55%」这类二手数字，用上游自己算的：

| | v1.6.0 | v1.7.0 |
|---|---:|---:|
| 全国节点数 | 17,333 | 17,841 |
| 全国边数 | 19,031 | 19,529 |
| **主连通分量节点数** | **11,423（占 65.9%）** | **11,695（占 65.6%）** |
| 连通分量总数 | 1,096 | 962 |
| 孤岛节点 | 2,161 | 1,985 |
| 缝合处 n_stitch / 联络 n_tie | 400 / 7 | 158 / 6 |

按系统分岛：v1.6.0 北海道 37、東 328、西 725、沖縄 6 → v1.7.0 北海道 38、東 320、西 597、沖縄 7。

**含义**：三分之一的节点不在主网里。因此 `net_edges` 必须带 `connectivity_confidence`，且**不能**直接拿来做拓扑分析（N-1、介数中心性等）。

⚠️ `index.json` 的 `regions` 数组里含一个 `id="all"` 的**全国缝合结果**，它不是第 11 个区域。求和时必须排除，否则全部翻倍（本项目已踩过）。
- **无线路阻抗 R/X/B、无变压器分接** —— 作者明示「非运行级」。本项目因此不做潮流计算，只显示 OCCTO 的潮流实绩。
  ⚠️ 但**机组成本不缺** —— `docs/data/generators.geojson` 有完整调度参数（见下）。
- 电压等级与官方公开信息的一致率、变电所召回率上游有公开验证数字，引用前回原仓库复核。


### ★ 最有价值的图层在 source 侧，不在 bundle 侧

`source/*/docs/data/` 下的 **`generators.geojson`（688 个 ≥10MW 机组）** 是本源里
对建模最有用的东西，但它**不在 release bundle 里**，所以首轮 ingest 的
`distribution_counts` 统计不到它，差点被完全忽略。

它带的属性：
```
capacity_mw, p_min_mw, ramp_up/down_mw_per_h, min_up/down_time_h,
startup_cost_jpy, fuel_cost_per_mwh_jpy, heat_rate_kj_per_kwh,
co2_intensity_kg_per_mwh, planned/forced_outage_rate, capacity_factor, dispatchable
```

与 `occto_unit` 做发电所匹配时，**用 `generators`（池 688）比用 `plants`（池 19,138）
命中更高**（56.6% vs 45.6%）—— 因为层级对得上。详见 `pipeline/structured/PLANT_MATCHING.md`。

**待办**：ingest 的 metadata 应该把 source 侧 `docs/data/` 的 11 个图层也统计进去
（现在只统计 bundle 侧的 4 个）。见 `TODO.md`。
