# エリア需給実績 — 区域逐时需给

## 概要

10 社各自公开的区域需给实绩。**需求侧的主力源**，也是电源构成（太阳光/风力/水力/火力/原子力/揚水/融通）分项的来源。

| 项 | 值 |
|---|---|
| 覆盖 | 10 区域 |
| 颗粒度 | 逐时（部分社 5 分钟） |
| 历史 | FY2016 起 |
| 坐标 | 无（区域值） |
| 编码 | Shift_JIS 系（原样落盘，不转码） |

## ★ 两种格式并存（先读这段，否则会白忙）

### FY2024 以降 —— 全国统一命名

```
eria_jukyu_{YYYYMM}_{NN}.csv     每月一个文件
```

`NN` 是事业者代码：**01**北海道 **02**東北 **03**東京 **04**中部 **05**北陸 **06**関西 **07**中国 **08**四国 **09**九州 **10**沖縄。

各社**只有存放目录不同**，所以按月枚举 URL 就能取：

| NN | 事业者 | 统一格式存放目录 | 起始 |
|---|---|---|---|
| 01 | 北海道電力ネットワーク | `hepco.co.jp/network/con_service/public_document/supply_demand_results/csv/` | 2024-04 |
| 02 | 東北電力ネットワーク | `setsuden.nw.tohoku-epco.co.jp/common/demand/` | 2024-02 |
| 03 | 東京電力パワーグリッド | `tepco.co.jp/forecast/html/images/` | 2024-02 |
| 04 | 中部電力パワーグリッド | `powergrid.chuden.co.jp/denki_yoho_content_data/` | ⚠️ 只有当月，见「已知坑」 |
| 05 | 北陸電力送配電 | `rikuden.co.jp/nw/denki-yoho/csv/` | 2024-04 |
| 06 | 関西電力送配電 | `kansai-td.co.jp/interchange/denkiyoho/area-performance/` | 2024-04 |
| 07 | 中国電力ネットワーク | `energia.co.jp/nw/jukyuu/sys/` | 2024-01 |
| 08 | 四国電力送配電 | `yonden.co.jp/nw/supply_demand/csv/` | 2024-04 |
| 09 | 九州電力送配電 | `kyuden.co.jp/td_area_jukyu/csv/` | 2024-04 |
| 10 | 沖縄電力 | `okiden.co.jp/business-support/service/supply-and-demand/csv/` | 2024-02 |

### FY2023 以前 —— 各社各不相同

命名规则和时间切分方式都不一样。从归档页面刮 `.csv` 链接来取：

| 事业者 | 归档入口 | 文件名 |
|---|---|---|
| 北海道 | `.../supply_demand_results/` | `sup_dem_results_{FY}_{n}q.csv` |
| 東北 | `setsuden.nw.tohoku-epco.co.jp/download.html` | `juyo_{FY}_tohoku[_{n}Q].csv` |
| 中部 | `getFilesInfo.php`（JSON） | `eria_jukyu_{FY}.zip`、`{FY}_areabalance_current_term.csv` |
| 北陸 | `rikuden.co.jp/nw_jyukyudata/area_jisseki.html` | `area_jisseki_rikuden{YYYYMM}.csv` |
| 関西 | `kansai-td.co.jp/denkiyoho/area-performance/past.html` | `area_jyukyu_jisseki_{FY}.csv` |
| 中国 | `energia.co.jp/nw/service/retailer/data/area/` | `kako-{FY}.csv` |
| 九州 | `kyuden.co.jp/td_area_jukyu/jukyu.html` | `area_jyukyu_jisseki_{FY|H28..H30}_{n}Q.csv` |
| 沖縄 | `.../supply-and-demand/` | `jukyu/csv/{FY}.csv` |
| **東京 / 四国** | **未找到** | ⚠️ 见「已知坑」 |

## 获取

**类型：`auto`**（不需要凭据）

```bash
python -m pipeline.sources.area_jukyu.probe            # 重探下载入口（改版后先跑这个）
python -m pipeline.sources.area_jukyu.ingest           # 全社全期间
python -m pipeline.sources.area_jukyu.ingest --utility tokyo
python -m pipeline.sources.area_jukyu.ingest --skip-legacy
python -m pipeline.sources.area_jukyu.ingest --recent 2   # 当月＋前月强刷（定时任务用）
```

`probe.py` 只做一件事：列出「哪个页面挂了哪些 CSV」。网站改版导致取不到时，先跑 probe 更新候选 URL。

### 更新节奏与调度（2026-08-27 起入日次调度）

- 上游是**当月累积 CSV**，各社约日次滚动更新当月文件。
- ingest 默认「已存在就跳过」，所以当月文件会**停在首次取得的那天**
  （OCCTO 当日文件同款坑）。`--recent N` 让最近 N 个月即使已存在也强刷，
  并跳过 legacy。
- 已挂进 Dagster `backfill_daily`（01:20 JST，`AreaJukyuWindow(recent=2)`，
  见 `orchestration/schedules.py`）：每天 10 社 × 2 件 ≈ 6–7MB 下载、原地覆盖，
  净增长约 3.3MB/月。**下游（disaggregate → sites）不在调度里，仍手动**。
- 进调度的最实际理由：**中部只挂当月＋前月**，跨月忘拉就是永久缺口
  （raw 不可复原）。

## 落盘

```
storage/raw/area_jukyu/{utility}/{原文件名}
```

## 实测（2026-08-01 取得，共 487 文件 / 66.3MB）

| 社 | 代码 | 统一格式 | 归档 | 容量 |
|---|---|---:|---:|---:|
| 北海道 | 01 | 28 | 31 | 6.9MB |
| 東北 | 02 | 29 | 43 | 9.7MB |
| 東京 | 03 | 31 | **0** | 3.8MB |
| 中部 | 04 | 2 | 13 | 7.4MB |
| 北陸 | 05 | 29 | 76 | 6.9MB |
| 関西 | 06 | 29 | 8 | 7.7MB |
| 中国 | 07 | 31 | 8 | 9.1MB |
| 四国 | 08 | 29 | **0** | 3.1MB |
| 九州 | 09 | 29 | 32 | 8.2MB |
| 沖縄 | 10 | 31 | 8 | 6.8MB |

## 许可

| 字段 | 值 |
|---|---|
| `source_url` | 各社でんき予報 / エリア需給実績 页面（见上表） |
| `licence` | **TO VERIFY —— 10 社不统一，需逐社确认** |
| `redistributable` | **TO VERIFY（逐社）** |

⚠️ 逐社确认完成前，本源的衍生产物**不推 CDN**，只在本地开发使用。

## 已知坑

### 東京 / 四国 的 FY2023 以前尚未取得

这两社的归档页面是 JS 驱动、没有静态链接，或者归档页面本身还没找到。统一格式（FY2024 起）能取到，所以**时间序列的起点会是 2024 年**。metadata 的 `legacy_missing` 会标出来，别当成已经取全了。

下一步值得试的方向：各社的「過去の電力使用実績データのダウンロード」系页面；以及 JS 实际请求的 JSON（可能和関西的 `filelist.json` 是同一套结构）。

### 中部只有当月，过去分在年度 ZIP 里

中部的 `denki_yoho_content_data/` **只放当月和前月**。按 2024-04 起枚举会全部 404，而且会撞上「连续 404 就打断」的逻辑，静悄悄地 0 件收场。过去分是**年度 ZIP** + 年别 CSV，清单只出现在 `getFilesInfo.php` 这个 JSON 里。

**「统一命名所以全社一套取法」这句话只有中部不成立。** 某社 0 件结束时，那是失败不是成功。

### 空 200 会伪装成「取到了」

这个站群**不存在的月份也返回 HTTP 200，body 为空或极小**。不按字节数判断就会攒一堆 0 字节文件。ingest 把 200 字节以下算作缺失。

### 関西和中国必须带 Referer

`kansai-td.co.jp/interchange/...` 和 `energia.co.jp/nw/jukyuu/sys/` 不带 Referer 会被挡。ingest 里按社保存了各自的 Referer。

### 関西的下载清单是 JSON

`/interchange/denkiyoho/area-performance/filelist.json` 以 `{latest: [...], past: [...]}` 的形式给出文件名和大小。URL 枚举失效时，读这个 JSON 更可靠。

### 中国的统一格式从 2024-01 开始

其他社按年度（2024-04）切换，中国電力ネットワーク的 JS 里是 `min_y = 2024`，即历年 1 月起。起始月按年度写死会漏掉 3 个月。

### 其他

- **时刻边界**：断面归属（时段开始还是结束）各社可能不同。首次解析时用太阳光出力曲线和日出日落对照验证。
- **卒 FIT 表后光伏**在需求实绩里已经扣除，是**净值**，不要重复计算。
- **抓取纪律**：这是公开网页不是 API。串行 + 间隔 + 可识别 UA。历史回填只做一次，之后只取增量。
