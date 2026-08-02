# 国土数値情報 発電施設（P03）

## 概要

国交省的**政府开放数据**。唯一一个「按电源种分层 + 带坐标 + 开放许可」的发电设施点数据集。

| 项 | 值 |
|---|---|
| 版本 | **P03-13（2013 年度 / 平成25年度）** |
| 体积 | zip 1.4MB，解压后 42MB |
| 条数 | **12,814** |
| 坐标 | ✅ **有**（点，JGD2000 系，见各 `.prj`） |
| 许可 | 国土数値情報 利用約款（政府开放数据，需注明出处） |

## 分层与实测条数

| shapefile | 内容 | 条数 |
|---|---|---:|
| `PhotovoltaicPowerPlant` | 太陽光 | 10,654 |
| `GeneralHydroelectricPowerPlant` | 一般水力 | 698 |
| `WindPowerPlant` | 風力 | 417 |
| `BiomassPowerStation` | バイオマス | 400 |
| `ThermalPowerPlant` | 火力 | 390 |
| `PondInformation` | 揚水的调整池 | 92 |
| `NuclearPowerPlant` | 原子力 | 68 |
| **`PumpedStorageHydroelectricPlant`** | **揚水** | **46** |
| `PowerPlantComplex` | 複合型 | 30 |
| `GeothermalPowerPlant` | 地熱 | 19 |

**揚水 46 座** —— 日本揚水总数就是 40 多座，等于齐了。这一项直接完成了 C3 的揚水部分。

## ★ 字段是编码的，必须查表

dbf 的字段名是 `P03_0001` 这种编码，**不是日文**。实测解码：

| 字段 | 含义 | 例 |
|---|---|---|
| `P03_0001` | **事業者名** | 沖縄電力株式会社 |
| **`P03_0002`** | **発電所名** ← 匹配用这个 | 具志川火力発電所 |
| `P03_0003` | 所在地 | 沖縄県うるま市字宇堅657番地 |
| `P03_0004` | 号機 | 1 |
| `P03_0005` | 運開年月 | 1994-03 |
| `P03_0006` | 出力 | 156.0 |
| geometry | **经纬度点** | (127.87508, 26.38031) |

⚠️ 首次解析时误取了 `P03_0001`（事業者名）当发电所名，匹配率算成 0%。
**`PondInformation` 的 schema 不同**（`P03_0301`…），没有 `P03_0002`。

## 获取

**类型：`auto`** —— 无凭据。页面上的 zip 链接是 JS 生成的，但**下载 URL 有固定规律**：

```
https://nlftp.mlit.go.jp/ksj/gml/data/P03/P03-13/P03-13.zip
```

```bash
python -m pipeline.sources.ksj_p03.ingest
```

## 落盘

```
storage/raw/ksj_p03/P03-13/P03-13.zip
storage/raw/ksj_p03/P03-13/extracted/SHP/*.shp|dbf|shx|prj
storage/raw/ksj_p03/P03-13/extracted/GML/P03-13-g.xml
```

## 已知坑

- **zip 内目录名是 Shift_JIS 编码的日文**，Python 按 cp437 解成乱码。ingest 因此**不保留原目录结构**，按英文 shapefile 名平铺。
- **dbf 编码是 cp932 且有严格模式解不了的字节**。必须
  `shapefile.Reader(..., encoding="cp932", encodingErrors="replace")`，
  用 `shift_jis` + strict 会抛 `dbfFileException`。
- **2013 年度冻结**。对**长寿命资产（水力/揚水/原子力/大型火力）坐标完全可用**，
  对 2013 年后爆发的光伏/风力、以及新建燃气电站**覆盖不足**。
- **相对 AJG 的增量为 0**（实测）：与 occto_unit 匹配 57.9%，但匹配到的 132 个
  **全部已被 AJG 覆盖**，独有贡献 0pt。详见 `pipeline/structured/PLANT_MATCHING.md`。
  它的价值在**交叉验证**（政府数据 vs OSM 众包）和**揚水/池的独立清单**，不在补坐标缺口。
