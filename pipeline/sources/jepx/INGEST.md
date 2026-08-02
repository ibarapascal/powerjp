# JEPX — 现货市场价格

## 概要

系统价与区域价，30 分颗粒度。价格层是「供需空洞」计算的经济信号：区域价差是联络线约束显现的地方。

| 项 | 值 |
|---|---|
| 颗粒度 | 30 分 |
| 历史 | 历年 CSV 可下载 |
| 格式 | CSV |

## 获取

**类型：`auto`** —— 公开 CSV，无需凭据，但 **Referer 必须带**。

站点用 XHR 取数，两个 GET 接口（不是页面上那个 POST 表单）：

```
/js/get_graph_year.php?dir=spot_summary          → "latestYear,oldYear"
/js/csv_read.php?dir=spot_summary&file=spot_summary_{year}.csv
```

```bash
python -m pipeline.sources.jepx.ingest --all      # 全年度
python -m pipeline.sources.jepx.ingest --year 2024
python -m pipeline.sources.jepx.ingest --years 3
```

另有 `dir=spot_index`（JEPX インデックス）同样接口可取，v0 未取。

## 落盘

```
storage/raw/jepx/{年度}/spot_summary_{年度}.csv
```

## 实测数字（2026-08-01 取得）

| 项 | 值 |
|---|---|
| 年度范围 | **FY2005–FY2026（22 年度）** |
| 合计行数 | **374,086** |
| 合计体积 | 39.9MB |
| 编码 | Shift_JIS（原样落盘） |

**`{year}` 是年度不是历年**：`spot_summary_2024.csv` = 2024-04-01 ～ 2025-03-31。

行数校验很干净：平年 **17,521**（365×48+1）、闰年 **17,569**（366×48+1）。
例外只有两个，都合理：FY2005 = 17,473（市场起步年未满）、FY2026 = 5,953（进行中）。

## 列构成

```
受渡日, 時刻コード, 売り入札量(kWh), 買い入札量(kWh), 約定総量(kWh),
システムプライス(円/kWh),
エリアプライス{北海道,東北,東京,中部,北陸,関西,中国,四国,九州}(円/kWh),
売りブロック入札総量, 売りブロック約定総量, 買いブロック入札総量, 買いブロック約定総量
```

⚠️ **エリアプライス 只有 9 个地域，没有沖縄** —— 沖縄没有联络线，不在 JEPX 区域价对象内。
当成 10 区域处理必然出错。

## 许可

| 字段 | 值 |
|---|---|
| `source_url` | JEPX スポット市場取引結果 |
| `licence` | **TO VERIFY** —— JEPX 利用规约 |
| `redistributable` | **TO VERIFY** |

## 已知坑

### 缺 Referer → 空 200

不带 `Referer: https://www.jepx.jp/electricpower/market-data/spot/` 时，
两个接口都返回 **HTTP 200 但 body 为空**。不是报错，是静默返回空——
按状态码判断成功会得到 0 字节文件。

### 「CSV 含全年未筛选片段」这条不成立

立项时的调研笔记里记着「CSV 含全年非筛选片段」。**实测不是这样**：
每个文件的范围恰好是该年度（4/1〜翌 3/31），无多余数据。
调研笔记那条可能指的是另一个接口或已变更。以本文件的实测为准。

### 沖縄不在エリアプライス里

见上「列构成」。9 地域，不是 10。

### 价格不是物理实时

前日/当日公布的市场数据，与潮流的 30 分实绩不是同一种「实时」。对外表述不要混为一谈。
