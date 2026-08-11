# OCCTO 系統情報サービス — 地内基幹送電線潮流実績

## 概要

基干送电线的**逐线**潮流实绩，30 分颗粒度。**网侧唯一的运行态数据源**，也是「潮流 ÷ 运用容量 = 利用率」的分子。

| 项 | 值 |
|---|---|
| 颗粒度 | 30 分 × **48 断面/日** |
| 覆盖 | **10 区域全部**（沖縄也有） |
| 历史 | 2023-01 起 |
| 更新 | 每 30 分钟，服务器侧约 2 分钟时滞 |
| 编码 | Shift_JIS（原样落盘，不转码） |

## ★ 不需要账号 —— 立项时的判断是错的

立项阶段记的是「需要 occtonet3 账号，属于 `assisted`」。**实测推翻**：

系統情報サービス的「公表」部分用的是系统自动分配的匿名身份 ——
`ユーザID: RP11SSO` / `ユーザ名: 公表 利用者`，URL 路径就是 `/public/`。
POST 一次 LOGIN 端点拿到 session cookie 即可，**不需要任何凭据**。

因此本源是 **`auto`** 类型。

## 获取

```bash
python -m pipeline.sources.occto_flow.ingest                  # 最近 7 天（默认）
python -m pipeline.sources.occto_flow.ingest --days 30
python -m pipeline.sources.occto_flow.ingest --date 20260731
python -m pipeline.sources.occto_flow.ingest --from 20250401 --to 20250430
python -m pipeline.sources.occto_flow.ingest --all            # 2023-01-01 至今
python -m pipeline.sources.occto_flow.ingest --area 3 --days 3
python -m pipeline.sources.occto_flow.ingest --days 2 --include-today --force  # 30 分增量
```

### 调用流程（四步）

```
1. POST  …/SD/LOGIN_login    空 body           → session cookie
2. POST  …/SD/CB01S020C      actionSubType=printOK
                             头 `sdReqType: AJAX`
                             → JSON: root.bizRoot.header.{downloadKey,requestToken}.value
3. POST  …/SD/CB01S020C      actionSubType=download + 上一步两个 token
                             → CSV（Shift_JIS）
4. POST  …/SD/LOGOUT                            释放 session
```

第 2 步的 token 是**一次性**的：每个「区域 × 日期」都要重走 2→3。
session（第 1 步）可以复用，所以一次 login 能批量取多天。

### 请求体要点

```
fwExtention.pathInfo = CB01S020C     fwExtention.formId = CB01S020P
fwExtention.actionType = reference   fwExtention.actionSubType = printOK | download
tgtNngpHdn      = YYYYMMDD           对象年月日
areaCdAreaSumNon= NN                 对象区域（01 北海道 … 10 沖縄）
tgtAreaHdn      = NN                 同上
tgtDaHdn        = 00                 对象电压（00=全電圧，也可 500/275/220/187/132）
searchReqHdn    = [検索条件]対象年月日:YYYY/MM/DD, 対象エリア:{日文区域名},対象電圧:全電圧
```

⚠️ `searchReqHdn` 是**服务端会核对的检索条件文字**，格式必须照旧 ——
注意第一个逗号后有空格、第二个没有。区域名必须是日文原文。

**协议来源**：`thgcMtdh/grid-api` 的 PHP 实现（powerflowmap 的后端）。
该仓库**没有 license**，因此本项目只采用「接口怎么调」这类事实，代码独立编写。

## 落盘

```
storage/raw/occto_flow/{YYYY-MM}/chinai_kikan_jisseki_{YYYYMMDD}_{NN}.csv
```

按月分目录，避免上千个日期目录。

## CSV 结构

```
対象年月日, 対象エリア, 電圧, 送電線名, 潮流方向(正方向), 00:00, 00:30, …, 23:30
2026/07/31,東京,500,新京葉線,新京葉 → 新古河,3510,3110,2790,…
```

- 一行一条线，48 列断面值（单位应为 MW「未在页面上确认，按量级推断」）
- **`潮流方向(正方向)` 给出正方向的起终点**（如「新京葉 → 新古河」），数值为负即反向流动 —— 这是把潮流挂到几何上时的方向依据
- 東京区域约 88 行；区域间差异大（北陸/四国等基干线少）

## 许可

| 字段 | 值 |
|---|---|
| `source_url` | https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/LOGIN_login |
| `licence` | OCCTO 利用条件：**可自由使用，须注明出处**；经编辑加工的还须另行注明已加工 |
| `redistributable` | **大概率 yes（附出处 + 加工声明）**，正式发布前把利用条件原文存档复核 |

出处表述来自 OCCTO 系统的免責事項画面原文：

> 本機関ウェブサイトで公開している情報…は、自由に利用できますが、その際は、出典を記載してください。また、コンテンツを編集・加工等して利用する場合は、出典とは別に、編集・加工等を行ったことを記載してください。

实质等同 CC-BY。这条同时适用于 `occto_capacity`。

## 已知坑

### 抓取量很大，务必用增量

一格 = 一个「区域 × 日期」，每格 **2 次 POST**。全量回填 2023-01 至今：

```
10 区域 × 约 1,300 天 × 2 次 = 约 26,000 次请求
按纪律的 2 秒间隔 ≈ 14 小时
```

所以：**日常只跑 `--days 7`**，全量回填单独在后台慢慢跑。脚本按文件存在与否跳过，**中断后重跑即续传**。

### 当日数据 23:32 之后才齐

默认不取当天（`--days N` 的窗口结束于昨天）。30 分钟增量任务使用
`--include-today --force`，把当日文件反复覆盖以接收新断面；日次穴埋仍不取当天。
取当天会拿到不完整的一天，这是预期行为，publish 会裁掉末尾尚未到来的空断面。

### 空 token ≠ 错误

未来日期或未公开的日期，第 2 步会返回**空的 downloadKey/requestToken**。这是正常的「没数据」，不是失败，metadata 里计入 `no_data`。

### 沖縄也有数据

沖縄电网与本土不相连，原以为没有「地内基幹送電線」条目，**实测是有的**（文件约 7KB，比本土小）。不要在代码里硬编码跳过它。
