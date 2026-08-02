# FIT/FIP 認定・導入量（都道府県別 / 市町村別）

## 概要

経産省「再生可能エネルギー 事業計画認定情報」公表サイト（fit-portal.go.jp）。**无凭据。**

**市町村级的再能源认定量与导入量，按电源种和容量档细分** —— 分布式光伏/风电的空间分布，
区域级需给实绩完全看不到这一层。

| 表 | 内容 | 文件数 |
|---|---|---:|
| A表 | 都道府県別 認定・導入量 | 35 |
| **B表** | **市町村別 認定・導入量** ← 主目标 | 35 |
| C表 | 買取電力量及び買取金額の推移 | 1 |

**季度更新，回溯到 2017-06。** xlsx，B 表约 1.7MB/期，6 个 sheet。

## B 表结构（实测 2025-12 版）

```
行：北海道札幌市 / 北海道函館市 / …（全国市町村）
列：太陽光発電設備  10kW未満（うち自家発電設備併設）
                    10kW以上（うち 50kW未満 / 50–500kW / 500–1,000kW /
                              1,000–2,000kW / 2,000kW以上）
    風力発電設備    20kW未満 / 20kW以上（うち洋上風力）
    中小水力 / 地熱 / バイオマス
sheet：表B①-1・①-2（件数）/ 表B②-1・②-2 / 表B③ / 表B④
```

## ⚠️ 没有坐标

**市町村级聚合，不是逐设施清单。** 它回答「哪个市町村装了多少」，
不回答「具体那台在哪」。补不了 `plant_xref` 的坐标缺口。

## 获取

**类型：`auto`**。站点是 Salesforce 架构：

```
GET https://www.fit-portal.go.jp/PublicInfoSummary              解析出下载项
GET https://www.fit-portal.go.jp/servlet/servlet.FileDownload?file=<15位ID>
```

⚠️ **文件 ID 每期公表都会变**，所以 ingest **每次重新解析公表页取 ID，不写死**。

```bash
python -m pipeline.sources.fit.ingest              # 全部 A/B/C
python -m pipeline.sources.fit.ingest --table B    # 只要市町村别
python -m pipeline.sources.fit.ingest --latest 4   # 只要最近 4 期
```

## 落盘

```
storage/raw/fit/{A|B|C}/{表}_{YYYYMM}.xlsx
```

## 许可

| 字段 | 值 |
|---|---|
| `source_url` | https://www.fit-portal.go.jp/PublicInfoSummary |
| `licence` | **TO VERIFY** —— 経産省 公表資料 |
| `redistributable` | **TO VERIFY** |

## 已知坑

- **表名里是全角数字**（`（２０２５年１２月末時点）`），解析期号要先转半角。
- 下载响应必须校验是不是 zip（xlsx 魔数 `PK`）—— ID 失效时会返回 HTML 错误页而不是报错。
- **它是「認定量」和「導入量」两套数**，不要混：認定 = 批了，導入 = 实际投运。
- 与 `ksj_p03` 互补：P03 给 2013 年前的**逐设施坐标**，FIT 给 2017 年后的**市町村聚合量**。
  两者**都不能**单独给出「2013 年后新建设备的坐标」——那是当前的真实缺口。
