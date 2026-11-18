# Global Energy Monitor — 机组级发电属性

## 概要

机组级（unit-level）的发电资产属性：燃料、容量、运开年、坐标。**火电按 LNG/煤/石油拆分的燃料字段来自这里** —— 不拆分则燃料制約与 merit order 无从谈起。

日本官方侧没有对应物：電力調査統計是县级汇总无逐所明细；HJKS 只有 10 万 kW 以上机组的停机事件。

## 获取

**类型：`manual`** —— 需人工操作。**2026-08-01 调查后仍未取得，下面记录已知与未知。**

### 已确认（省得下次重走）

| 项 | 结论 |
|---|---|
| `/download-data` 页 | 只是各 tracker 的**导航页**，**没有任何直挂的 xlsx/zip** |
| 项目页的「Download data」 | 指向**页内锚点** `#download`，不是外链 |
| `#download` 区块 | 是 `embed-tabs-wrapper`，**内容由 JS 加载，curl 抓 HTML 看不到** |
| 页签结构 | 两个：`Download data` / `Summary tables`，默认显示后者 |
| `Summary tables` | 5 个 **Google Sheets** 汇总表（公开可看，但只有按技术/地区的聚合容量，**不含机组级**） |
| 最新版本 | **March 2026 release** |
| 推荐引用 | `Global Integrated Power Tracker, Global Energy Monitor, March 2026 release.` |

### 未确认

**到底要不要填表下载 —— 没验证出来。** 尝试过程：浏览器能打开项目页并定位到
`#download` 区块，但**点开 `Download data` 页签时渲染进程连续两次 CDP 超时**
（页面很重）。curl 走不通（内容是 JS 加载的）。

GEM 历来的做法是要填姓名/邮箱/机构才给 xlsx，但**这是印象不是实证**，别当结论用。

### 下次怎么走

1. 在配置好的浏览器里打开 https://globalenergymonitor.org/projects/global-integrated-power-tracker/
2. 滚到「What's inside?」，点 **`Download data` 页签**（不是 Summary tables）
3. 看是直接给下载链接，还是弹表单
4. 如果是表单：按表单要求填写
5. 下载后放到落盘路径，并把文件名/版本/日期记到本文件末尾的「取得记录」

⚠️ 页面很重，浏览器自动化容易卡渲染进程。**建议人工点，别耗在自动化上。**

## 落盘

```
storage/raw/gem/{download_date}/
```

## 许可

| 字段 | 值 |
|---|---|
| `source_url` | Global Energy Monitor — Global Integrated Power Tracker |
| `licence` | **CC BY 4.0** —— 见 Global Energy Monitor 的 licence 页面 |
| `redistributable` | **yes** —— 需注明出处，建议引用 *Global Integrated Power Tracker, Global Energy Monitor, March 2026 release* |

许可已按 `ATTRIBUTION.md` 的 2026-08-11 核查结论同步。GEM 派生属性可以进入发布产物，
但仍不能把 GEM 的许可误写成 ODbL；发布元数据要同时列出各来源。

## 已知坑

- **下载入口是 JS 嵌入的**，服务端 HTML 里没有。想用脚本自动化，得先解决这一层。
- **页面很重**，浏览器自动化容易触发渲染超时。
- **GEM 不收变电站**。点资产的出处分工：发电站 = GEM（机组级属性），变电站 = OSM/All-Japan-Grid（坐标）+ 各社空容量表（属性）。
- 与 All-Japan-Grid 的发电所做空间 join 时会有未匹配残留。未匹配率记进 asset metadata 并输出清单，不要静默丢弃。
- 交叉校验：各县装机容量合计 vs 電力調査統計县级汇总，用于发现 join 错配。

## 取得记录

| 下载日期 | 文件名 | 版本 | 备注 |
|---|---|---|---|
| — | — | — | 尚未取得 |
