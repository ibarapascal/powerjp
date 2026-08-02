# OCCTO 連系線運用容量 — 联络线传输约束

## 概要

区域间联络线的运用容量，年度公告。**这是「供需空洞」计算里网络约束的分母** —— 潮流 ÷ 运用容量 = 利用率，也是判断「电送不送得过去」的直接依据。

在所有网侧参数里**确定性最高**：官方公告、免登录、口径明确。

| 项 | 值 |
|---|---|
| 颗粒度 | 逐联络线 |
| 频率 | 年度公告，含未来数年计划值 |
| 格式 | 公告页面 / PDF |

## 获取

**类型：`auto`**（免登录公开公告）

⚠️ OCCTO 的 news 列表**是 JS 驱动，静态链接取不到**，刮 HTML 什么也拿不到。
但资产 URL 有规律，所以按年度枚举直接请求：

```
https://www.occto.or.jp/assets/renkeisenriyou/oshirase/{FY}/files/
    oshirase_1_{FY+1}-{FY+10}_unyouyouryou.pdf   ← 主目标：10 年分的运用容量
    oshirase_2_{FY}_sansyutsuhouhoukekka.pdf     算出方法与结果
    oshirase_3_{FY}_setsubiteishiji.pdf          设备停止时
    oshirase_4_{FY}_minaoshi.pdf                 修订
    oshirase_5_{FY}_sagyouteishi_eikyou.pdf      作业停止的影响
```

```bash
python -m pipeline.sources.occto_capacity.ingest
python -m pipeline.sources.occto_capacity.ingest --fy 2025
```

## 落盘

```
storage/raw/occto_capacity/{FY}/oshirase_*.pdf
```

## 实测（2026-08-01 取得）

| FY | 運用容量 | 他 4 本 | 备考 |
|---|---|---|---|
| 2023 | ✅ 2024–2033 | 只有 2 个（2/3/5 是 404） | 命名稳定下来的前一年 |
| 2024 | ✅ 2025–2034 | ✅ 5 个齐全 | |
| 2025 | ✅ 2026–2035 | ✅ 5 个齐全 | 最新 |
| 2026 | ❌ 404 | — | 尚未公布（惯例是 3 月公布） |

合计 12 个 PDF / 27.4MB。**FY2022 以前命名不同**（同一套规律会 404），v0 不追。

## 许可

| 字段 | 值 |
|---|---|
| `source_url` | OCCTO 連系線運用容量 公告页 |
| `licence` | **TO VERIFY** —— 公开公告，但利用条件需确认 |
| `redistributable` | **TO VERIFY** |

## 已知坑

- **只有 PDF**。表格抽取是 structured 阶段尚未实装的任务。「取到了」不等于「能用了」。
- **news 列表是 JS 驱动**。刮 `/news/renkeisenriyou/` 得 0 件，URL 枚举是唯一出路。
- 404 时 OCCTO **会连着 404 一起返回一个 HTML 错误页**。不看 Content-Type 就保存的话，
  会攒下一堆后缀是 .pdf 的 HTML。ingest 用 `%PDF` 魔数挡掉了。
- 容量按**方向**区分（顺向/逆向不同值），别只存一个标量。
- 同一年度内可能因工事等有期间别的临时值，解析时保留期间字段而非取年度代表值。
- 与 `occto_flow` 的线路标识需要对齐；两者命名不一致时以本源为准建映射表，映射表进 `pipeline/core/`。
