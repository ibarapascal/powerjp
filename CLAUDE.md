# powerjp

> **目的**：把日本电力系统（发电 / 送电网 / 潮流 / 区域需给）的公开数据收进来，做成可视化与分析。
> **本文件** = 架构与约定的 SSOT。**本 repo 将来公开**，所以这里只写技术与数据；机器身份、凭据、非公开判断一律不写（见 `CLAUDE.local.md`）。
> **更新规则**：架构或约定变更时改这里；日常开发不动。新的坑写进 `LESSONS.md`。
> **引用关系**：数据源盘点的上游依据在会话外部的调研文档；各源的获取细节在 `pipeline/sources/{name}/INGEST.md`。

## 语言规范

- repo 内 `.md` 默认**中文**。数据源名称保留日文原文（エリア需給実績、系統情報サービス）；技术术语保留英文（asset、Parquet、tile）。
- **例外**：`README.md` 用**日文**（公开门面，主要读者在日本）；`ATTRIBUTION.md` 用**英文**（出典与许可，面向国际读者）。
- **commit message 一律日文**（标题与正文；2026-11-18 用户裁定，repo 公开后历史统一为日文）。

## 项目定位与范围

**做**：发电点 / 送电网几何 / 基干潮流 / 10 区域逐时需给 / 电源构成 / 价格，以及基于它们的计算层。

**明确不做**（写下来防止将来反复讨论）：

| 不做 | 原因 |
|---|---|
| 建筑颗粒度的需求推计 | 需求侧止于 **10 区域**。连带排除 JED / DECC / PLATEAU，也顺带消除了 CC-BY-NC 许可污染 |
| 配电级（6.6kV 馈线）几何 | 不公开，硬边界 |
| 运行级精确仿真 | 线路 R/X/B 无公开权威源（机组参数倒是有，见「已知边界」）；本项目定位是可视化与区域级分析 |

## 技术栈

| 层 | 选型 | 备注 |
|---|---|---|
| 编排 | 外部编排平台（2026-08-27 起） | 采集与前端 publish 的定时都由外部编排以 subprocess 调本 repo 的 CLI。本 repo 没有编排层 |
| 处理 | Python + uv | 脚本纯 CLI，不感知编排层 |
| 存储 | PostGIS（几何/拓扑） + Parquet（时序） | Parquet on disk 是 SSOT |
| 瓦片 | tippecanoe → `{z}/{x}/{y}.pbf` 目录 | **不用 PMTiles**：其 `addProtocol` 是 MapLibre API，Mapbox GL JS 不支持 |
| 分发 | Cloudflare R2（静态产物） | 后端不对公网暴露端口，只出站 |
| 前端 | React + Vite + **Mapbox GL JS** | Cloudflare Pages |

## 架构

### Ingest / Process 分界线 = `storage/raw/`

```
Ingest   → 文件落入 storage/raw/{source}/{date}/     方式不统一，可人工，不进自动化
──────────────────────── 分界线 ────────────────────────
Process  → 从 raw/ 出发，完全确定性、可重跑、可回填    全自动
```

文件到了 `raw/`，后面的一切都是确定性的。凭据、登录、注册全部留在分界线以左。

Ingest 三类：`manual`（人工下载放指定位置）/ `assisted`（提供凭据后脚本拉）/ `auto`（公开可直取）。每源在自己的 `INGEST.md` 里声明属于哪类。

### 四层 asset

```
raw          原样落盘，不可变，带抓取时间戳
  ↓
structured   统一 schema / 单位 / 时区 / 区域码
  ↓  net_nodes, net_edges, gen_units, ts_area, ts_flow, ts_price
derive       计算
  ↓  area_balance, flow_utilization, price_spread, （后续）供需空洞
publish      tiles/{z}/{x}/{y}.pbf + Parquet 分片 + manifest.json → R2
```

### ★ 哪一层是原本，哪一层是派生物

**只有 `raw/` 需要保存。其余全部可以删掉重建。**

| 层 | 性质 | 丢了会怎样 |
|---|---|---|
| `raw/` | **原本・SSOT** | ⛔ **不可复原**。上游只保留有限期间，过去的断面拿不回来 |
| `structured/` | 派生 | 重跑 pipeline 即可 |
| `publish/` | 派生（当前快照） | 同上 |

因此：

- `raw/` **按日期分文件、不覆盖**。30 分更新对当日文件用 `--force` 覆盖，
  但当日 CSV 是**累积**的（含到当时为止的全部断面），所以不丢历史
- `publish/` 是**当前 120 小时的快照**，被覆盖是正常的，不是数据丢失
- ⚠️ **分析用的时序档案还没有** —— 现在历史只存在于 raw 的 CSV/JSON 里，
  查询要逐文件解析。要做跨年分析得先把它们转成 Parquet（`TODO.md` S4〜S7）。
  **这是「数据要落档供未来分析」这件事目前唯一的缺口。**

### 推进顺序（进一层，看一层）

```
1. 点线网络   AJG 几何                → 地图上出现电网骨架
2. 潮流       OCCTO 30 分逐线          → 线动起来，粗细=容量，颜色=利用率
3. 需求       エリア需給実績 逐时       → 区域着色，时间轴可拖
4. 各类电     GEM 燃料拆分 + 电源分项   → 电源构成随时间变化
──── 以上纯搬运，不做推算 ────
5. 计算中间层  供需空洞 / 各类需求 / 按需加
```

**供需空洞的定义**（防止跑偏）：逐时 × 带网络约束的到达性洼地 —— 某区域某时刻本地供给够不够、要不要外部输入、通道容量够不够、何时最紧。**不是**「再生能源发电量 ÷ 当地需求」的年度能量账。

## 目录结构

```
powerjp/
├── CLAUDE.md              ← 本文件
├── CLAUDE.local.md        ← gitignore，一行 import（内容在 repo 外）
├── LESSONS.md             ← 踩坑记录
├── README.md              ← 日文，公开门面
├── ATTRIBUTION.md         ← 英文，数据源出典与许可
├── pipeline/              ← 纯 Python，不感知 Dagster
│   ├── core/              ← 共通工具（坐标、区域码、时区）
│   └── sources/{name}/    ← 每源一目录，含 INGEST.md
├── web/                   ← React + Vite + Mapbox（M1 起就存在，初期极简）
├── infra/                 ← docker-compose 等
├── storage/               ← gitignore
│   ├── raw/               ← 不可变原始落盘
│   └── structured/        ← Parquet 产物
├── docs/decisions/        ← ADR，每个架构决定一个文件
├── docs/OPERATIONS.md     ← 公开安全的部署、监控、刷新、回滚手册
└── docs/releases/         ← 发布事实记录（不放凭据或内部机器信息）
```

## 约定

1. **asset 名只用 ASCII** `[A-Za-z0-9_]`，可读性靠 docstring。
2. **`group_name = {layer}_{domain}`**，如 `raw_network` / `structured_timeseries` / `derive_balance`。
3. **每个 asset = 一个 subprocess 调用**，不用 IO Manager。Dagster 只记 metadata，不管理文件。
4. **metadata 必记**：行数、文件大小、路径、时间范围。可视化 asset 额外记 **`demo_url`** —— Dagster UI 里点一下直接开地图，这是主要的正确性验证入口。
5. **不写测试用例**，不引数据契约库，不做断言阻断。正确性靠人工 e2e 看地图；metadata 数字用于发现「这次比上次少一半」这类肉眼看不出的问题。
6. **时序一律 UTC 存储**，JST 只在展示层转。30 分結算断面的边界处理错一次全盘乱。
7. **`net_edges` 必须带 `connectivity_confidence` 字段** —— 上游几何源的接続召回率有限，不显式标注的话，任何拓扑分析都建在沙子上。
8. **发布原子性**：产物先全部推 R2（文件名带内容 hash），**最后**才更新 `manifest.json`。前端只信 manifest，因此永不读到半成品，也不需要刷 CDN 缓存。
9. **时序 asset 全部分区**（按月/按日 × 按事业者）：增量、可回填、失败只重跑单格。
10. **凭据永不进 git**（任何 repo）。走 `.env` + gitignore。

## 每个数据源必须留下的上下文

`pipeline/sources/{name}/INGEST.md` 是**这个获取渠道的维护手册**。渠道会失效
（网站改版、命名规则变、许可条件变），而失效时人往往已经忘了当初怎么打通的。
所以每个源的 INGEST.md **必须**有这五节：

| 章节 | 写什么 |
|---|---|
| **概要** | 这是什么数据、颗粒度、覆盖范围、历史起点、更新频率 |
| **获取** | ingest 类型（manual/assisted/auto）、CLI 用法、**完整调用流程**（端点、参数、必需的头） |
| **落盘** | `storage/raw/` 下的具体路径结构 |
| **许可** | `source_url` / `licence` / `redistributable` 三字段，取数当时填 |
| **已知坑** | 踩过的每一个坑，以及**为什么**（这节最值钱） |

另外强烈建议加：

- **实测数字** —— 件数、体积、行数。下次取数对不上就知道上游变了
- **未确认的事** —— 明确写出「什么没验证」，别让读者以为已经确认过
- **下次怎么走** —— 对没打通的源，写清楚试过什么、卡在哪、下一步从哪试

判断标准：**半年后一个不记得任何事的人，能照着它把数据重新取下来吗？**

## 运行

```bash
uv sync                                          # 环境（Python 固定 3.12.x）
uv run python -m pipeline.sources.{name}.ingest  # 单个源（定时采集由外部编排承担）
uv run python -m pipeline.publish.geojson --only dynamic   # publish（定时由外部编排承担）
```

> 编排（采集与 30 分 publish 的定时）2026-08-27 起移到外部编排平台；本 repo 只保留纯 CLI。
> 平台的实际位置与 job 名见本地 `DEPLOY.local.md` 开头的对照表。

> 端口与机器分工等**本地部署的选择**记在 `CLAUDE.local.md`（不进 git）——
> 那是我们这套环境的情况，不是项目本身的约束。单机也能跑完整流程。

### 冷启动与线上同步

代码仓库是技术代码与文档的 SSOT；私有计算节点只是带有 raw 历史和运行环境的运行镜像，
不能把它当成第二个代码分支。冷启动或接手任务时，先读 `docs/OPERATIONS.md` 的
change routing / cold-start sequence，再读本地 `DEPLOY.local.md`（gitignore；开头的对照表把本 repo 文档里的「外部编排平台」「私有计算节点」等中性说法对应到实际名字），
最后读本次涉及的数据源 `INGEST.md` 或 `web/` 文档。

前端改动只需要构建并部署 Cloudflare Pages；数据/API 改动需要把代码同步到私有计算节点，按
`ingest → structured/publish → R2 manifest` 的最小范围重跑；schedule 改动在外部编排侧进行。这里所说的“API”是 R2 的静态 manifest + hash object
契约，没有需要对公网重启的后端服务。完整的分流表、验收和安全边界以
`docs/OPERATIONS.md` 为准。

## ⛔ pipeline 是可重跑的规则，不是一次性处理

用户 2026-08-02 要求：**「所有对数据的调整，都是 pipeline 的调整而不是一次性的，
未来有类似的新数据，我们要也能处理好」**。

### 不写死上游的版本与下载日期

```python
AJG_TAG = "v1.6.0"                                     # ❌
GEM_XLSX = "Global-Integrated-Power-March-2026-II.xlsx" # ❌
paths.raw("gem") / "2026-08-01"                        # ❌ 下载日期
paths.raw("ksj_p03") / "P03-13"                        # ❌
```

固定之后，新数据取进来了也**照样读旧的**——不报错、不警告，
只是结果悄悄停留在过去。这是最难发现的一类错误。

改用 `paths.latest_version()` / `paths.latest_file()`，永远指向最新，
**取不到就抛异常**（宁可失败也不要静默用旧数据）：

```python
AJG_GLOB = "v*"
paths.latest_version("ajg", AJG_GLOB)              # v1.6.0 → 新版が来れば自動で追随
paths.latest_file("gem", "Global-Integrated-Power-*.xlsx")
paths.latest_version("ksj_p03", "P03-*")
```

⚠️ 版本比较**不能用字典序** —— `v1.10.0 < v1.6.0` になる。
`_ver_key()` が数字を取り出して数値として比べる。

### ingest も版を書かない

| 源 | 版の決め方 |
|---|---|
| `ajg` | GitHub Releases API の `latest`。⚠️ 取れなければ**例外**（古い版で成功したように見せない） |
| `ksj_p03` | 配布ページの zip リンクを解析。未検証の版なら警告（P03-07 以前は GML で形式が別） |
| 他 8 源 | もともと期間・ファイル一覧を毎回探索している |

再現性が要るときだけ `--tag` / `--version` で固定する。

**升级は必ず可視化する** —— 新しい版を見つけたらログに `★ 新しい版を検出` と出し、
metadata に `local_versions` を残す。structured 層は `latest_version()` で
ローカルの最新を選ぶので、**版が変わると下流の数字も変わる**。
どの版で出した結果なのかを後から辿れるようにしておく。

### 个别设施的知识放 xref，不放代码

代码里**不出现具体的发电所名**（実測：`pipeline/` 全体で 0 件）。
「柏崎刈羽の容量は 2,712MW」のような個別知識は `*_xref.json` に隔離する：

| ファイル | 何を人手で補うか |
|---|---|
| `plant_xref.json` | 座標が自動で引けない発電所 |
| `capacity_xref.json` | 設備容量が自動で引けない発電所 |

**xref は必ず「自動優先・人手は最後」**にし、**冗長検出**を付ける ——
上流が後から収録したら自動が勝ち、その項目は不要になったと報告される。
そうしないと人手の値が上流の改善を永久に上書きし続ける。

### 判定は規則で書く。個別対応で埋めない

このセッションで足した規則はすべて「次に似たデータが来ても効く」形にしてある：

| 規則 | どう一般化したか |
|---|---|
| 容量の取得 | GEM → AJG 発電所値 → AJG 行合算 → **座標引き** → xref → 観測下限 の 6 段 |
| 電源種の補完 | OCCTO が「その他」でも**照合先の電源種**で補う（`fuel_source` に出所） |
| 潮流の向き | 符号で幾何を反転（特定の線を名指ししない） |
| 容量の妥当性 | **実測出力を下回る容量は採らない**（物理制約） |
| 幾何の採否 | `seg_quality` の規則で判定（電圧不整合・長い直線代用は出力しない） |

### 新しいデータが来たときの確認

```bash
uv run python -m pipeline.structured.plant_coords --dump   # 覆盖率 + xref 冗長/失効
uv run python -m pipeline.structured.plant_verify          # 空間検証 4 項目
uv run python -m pipeline.structured.flow_route_probe
uv run python -m pipeline.structured.disaggregate
uv run python -m pipeline.publish.geojson
```

⚠️ **`plant_verify` を飛ばさない。** 覆盖率は上がったのに中身が壊れている、
という状態を検出できるのはこれだけ（LESSONS #23）。

## 抓取纪律

各社与 OCCTO 的页面是**公开网页不是 API**，被当成攻击会直接断掉整个数据源：

- 串行 + 间隔，不并发轰炸
- 带可识别 User-Agent
- 遵守 robots
- 历史回填一次性慢慢跑，之后每天只取增量
- `raw` 层幂等：同一断面重复抓取写同一路径 + 内容 hash 比对，不产生重复行

## 已知边界（引用数据前先读这里）

- **送电网接続关系不完整**：几何源的接続召回率有限，很多「空洞」是数据缺失不是真实缺失。做拓扑分析前必须先看 `connectivity_confidence`。
- **无线路阻抗 R/X/B、无变压器分接**：公开世界不存在权威源。因此本项目不做潮流计算，只展示 OCCTO 公开的潮流**实绩**。
- ⚠️ **机组成本不是缺口** —— AJG 的 `docs/data/generators.geojson`（688 个 ≥10MW 机组）带 `capacity_mw` / `ramp` / `startup_cost_jpy` / `heat_rate` / `co2_intensity` / `outage_rate` 等**完整调度参数**（典型值非实测）。原先写「无机组成本」是错的。
- **公开「实时」的真实边界**：需求 5 分钟、网侧 30 分钟、供给的**准实时**只有区域聚合。对外表述用「准实时（5–30 分钟延迟）」，不要说 real-time。
- **逐机组出力：公开的，但是日次公布不是准实时** —— OCCTO ユニット別発電実績公開システム 给「発電所コード / 発電所名 / ユニット名 / 発電方式 / 対象日 / 30 分 × 48 断面」，2024-03-25 起，每日 15:30 更新。⚠️ 立项调研笔记说「不存在」是**错的**，见 `pipeline/sources/occto_unit/INGEST.md`。
- ~~**逐变电站负载、配电级一切**：确认不公开，不要再找。~~ ⚠️ **2026-09-11 纠错：变电站级是公开的，只是不在 OCCTO 而在十家一般送配電事業者各自官网**（与 `occto_unit` 同一种错法）。两套表：①「地点別需要・系統潮流実績（変圧器）」＝变电所变压器二次侧 1 小时实测潮流，多数社到 22kV 站、北海道只到 66kV、東京・沖縄到 6.6kV bank；十社全有（九社 CSV、中部 Excel），全国齐的最新年度 FY2024。②「予想潮流・空容量一覧（変圧器）」＝台数・設備容量・運用容量・予想潮流・空き容量，九社静态 CSV 下到 6.6kV 配電用変電所（中部只有交互地图）。两表站名同体系，清洗后 join 率 95–100%。坐标官方不给，靠 AJG 按名匹配 22kV 以上 40–77%、6.6kV 站 22–58%。「空き容量」语义是逆潮流接入余量，不是加负荷余量。十社逐家的格式与解析要点待开工时整理进对应 `INGEST.md`。**许可与 OCCTO 不同**：各社系統情報页只有免责条款，站点通用著作権条款为「無断転載・複製禁止」（東京 HD /legal/、九州 sitepolicy 实读），原始 CSV 不可原样再发布；可行路径见 `TODO.md` C11。配电级 6.6kV **馈线几何**仍不公开，这条不变。

## Mapbox 注意

前端 token 会暴露在公开站点。**必须在 Mapbox 后台配 URL 白名单**限制到本项目域名，否则他人可盗用。免费额度 50k map loads/月，公开后需监控。

## 当前状态

### raw 层（7 个 asset）

> ⚠️ 2026-08-07 に `jepx` / `fit` / `occto_capacity` を削除（未使用 + 許諾の整理のため）。
> 経緯は `TODO.md` §1.1b、`ATTRIBUTION.md`「Removed sources」。

| asset | 类型 | 状态 |
|---|---|---|
| `raw_ajg` | auto | ✅ v1.7.0（2026-08-27），线 40,087 / 变电所 7,240 / 发电所 19,138；另有 `generators` 688 个 ≥10MW **带完整调度参数**。620.7MB，sha 253/253 |
| `raw_area_jukyu` | auto | ✅ 10 社 487 文件；**2026-08-27 起入日次调度**（`backfill_daily` 带 `recent=2` 强刷当月＋前月——当月文件是滚动累积的，中部只挂两个月）。⚠️ 東京·四国 FY2023 以前未取得；⚠️ FY2024+ 统一格式与 FY2023- 的 24 种异构表头并存 |
| `raw_occto_flow` | auto | ✅ 逐线潮流 30 分 × 10 区域，**匿名公表身份即可**。回填中 |
| `raw_occto_unit` | auto | ✅ **逐机组 × 30 分**，859 天 / 413,546 条 / 502MB，2024-03-25→今 |
| `raw_jepoc_hydro` | auto | ✅ 水力 1,801 座（帯 `output_kw`/発電方式）。⚠️ **无坐标** |
| `raw_ksj_p03` | auto | ✅ 発電施設 12,814 条 9 层，**带坐标**。⚠️ 对 AJG 独有贡献 0pt，但住所字段用于坐标→都道府県反查 |
| `raw_gem` | manual | ✅ GIPT March 2026，日本 12,074 行 52 列，**100% 坐标**。唯一有增量的坐标源（+5.0pt） |

### structured 层（4 个 asset，2026-08-01 打通）

**这是第一次出现依赖关系的层**（raw 层各 asset 互不依赖）。

| asset | 依赖 | 产物 | 关键指标 |
|---|---|---|---|
| `structured_plants` | occto_unit + ajg + gem + ksj_p03 | `plants/plants.json` | 185 座池 / 182 匹配，按発電量覆盖 **99.88%**；带 `coord_source`/`match_method` |
| `structured_plant_verify` | structured_plants | `plants/verify.json` | 四项校验 **(0,4,4,0)**（AJG v1.7.0 后 ② 多出 2 件，待人工确认——海田/福島いわき/田原/釧路 四件中新旧混在） |
| `structured_lines` | occto_flow + ajg | `lines/lines.json` | 464 条潮流线求出 **330 条（71.1%，AJG v1.7.0）**真实几何 |
| `structured_disaggregated` | area_jukyu + occto_unit + jepoc_hydro + gem + ksj_p03 | `disaggregated/sites.json` | 1,185 站（measured 9 / estimated 1,176）；带 `data_quality` |

⚠️ **覆盖率和正确率是两个 asset**。`plants` 报「匹配上多少」，`plant_verify` 报「匹配得对不对」——
名字对、坐标错**不掉覆盖率也不报错**（LESSONS #23），所以 verify 是独立的、会失败的一步。

### publish 层 + 前端（2026-08-02 起步）

`pipeline/publish/geojson.py` → `storage/publish/*.geojson`。

**★ 层的定义在 `pipeline/publish/spec.py`，那是唯一出处。**
pipeline / Dagster asset / schedule / 将来的配信脚本都从那里读，
不要在别处重写文件清单 —— 改一处漏一处，而且症状会离原因很远。

| 层 | 文件 | features | 未压缩 | 变更频率 |
|---|---|---:|---:|---|
| static | `grid_lines.geojson` | 40,936 | 17.8 MB | AJG 出新版时（现 v1.7.0） |
| static | `substations.geojson` | 9,298 | 2.2 MB | 同上 |
| static | `sites.geojson` | 106 | 28 KB | 同上；水力因许可未确认暂不发布 |
| dynamic | `flow_lines.geojson` | 684 | 1.5 MB | **30 分** |
| dynamic | `plants.geojson` | 183 | 216 KB | **30 分** |
| dynamic | `flow_stamps.json` | — | 3 KB | **30 分** |

`--only dynamic` 只重建下半：**1.7 MB / 0.8 秒**（全量 22.7 MB / 3.8 秒）。
30 分 schedule 用的就是这个 —— 送電線の走りは 30 分では変わらない。
`manifest.json` 记录各文件属于哪层与更新时刻，供配信侧按层设缓存。

⚠️ `flow_lines` は**区間単位**（線路単位ではない）。1 本の潮流線が
複数の電圧をまたぐため、区間ごとに実電圧を持たせて線幅を送電線と揃えている。
誤った幾何（電圧不整合・長い直線代用）は**出力しない**。詳細は `FLOW_MATCHING.md`。

`publish_r2` は `publish_geojson` の後段 asset。定时 job 只传 dynamic 层，初次上线或
static 变化时用 `--scope all`；数据对象名带内容 hash，`manifest.json` 最后写入。

前端在 `web/`（React + Vite + Mapbox GL JS，端口 8081）。
**扩展点只有 `web/src/layers.ts`** —— 侧边栏、图例、开关、信息面板全由它生成。
详见 `web/README.md`。

⛔ **改视觉之前必读 `web/REQUIREMENTS.md`** —— 用户口述的要求都在那里
（几何不简化 / 不因密而隐藏 / 潮流叠加只改颜色不改粗细 / 不画错的几何 / 重叠不偏移）。

⚠️ 数据**不放 `public/`**（build 会把 10 MB 复制进 dist）。
dev 由 vite 插件从 `storage/publish/` 配信，生产用 `VITE_DATA_BASE` 指向 CDN。

**derive 层尚未动工。**

### ⛔ publish 层的硬约束：**几何绝不简化**

用户 2026-08-02 明确要求：**「一定不要做简化」**。

不做 Douglas-Peucker，不按缩放级别抽稀，不合并顶点。
`lines.json` 的 317 条走线、35,469 个坐标点**原样发布**。

**理由不是大小，是原则**：项目一开始就定了「所有的线都要是真实几何」，
为省几百 KB 把真走线降级成折线近似，等于把花最大力气拿到的东西又丢掉。

大小也确实不构成问题：932 KB 的 JSON gzip 后约 200–300 KB（坐标数字重复度高），
CDN 传一次就缓存住；Mapbox 渲染三万多个顶点毫无压力。

⚠️ 这条约束**很容易被后来的人当成「性能优化」顺手改掉**，改之前必须先问。

### ⛔ 前端也不许因为「密」而隐藏要素

用户 2026-08-02 明确要求：**「太密没事，放大能看就行，不要缺东西」**。

- 不用 `minzoom` 在低缩放时藏掉图层
- 全国视图上 8,994 个变电所 + 624 个推算点重叠是**预期内**的，放大就分开了
- 「看不清」的解法是**颜色、透明度、叠放顺序**，不是删要素

这和「几何不简化」是同一条原则的两面：**保真优先于可读性**。

## 子文档索引

| 想了解 | 读 |
|---|---|
| **还有什么没做** | **`TODO.md`** ← 待办的 SSOT |
| **冷启动、前端/API 发布、R2、回滚、验收** | **`docs/OPERATIONS.md`** |
| **私有运维信息**（编排平台在哪、计算节点、代码同步、LaunchAgent、资源闸门） | 本地 `DEPLOY.local.md` 开头的「中性说法 → 实际名字」对照表；`CLAUDE.local.md`（均 gitignore，不进公开 repo） |
| 某数据源怎么拿、要什么凭据、有什么坑 | `pipeline/sources/{name}/INGEST.md` |
| 数据源出典与许可 | `ATTRIBUTION.md` |
| 踩过的坑 | `LESSONS.md` |
| 架构决定的来龙去脉 | `docs/decisions/` |
| 机组级出力怎么接坐标 | `pipeline/structured/PLANT_MATCHING.md` |
| 潮流怎么接线路几何 | `pipeline/structured/FLOW_MATCHING.md` |
| 非调度电源怎么摊分到逐站 | `pipeline/structured/disaggregate.py` 的 docstring |
