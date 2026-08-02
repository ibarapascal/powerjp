"""structured 层 assets —— raw 落盘 → 可消费的结构化产物。

## 这一层和 raw 的区别

raw 层每个 asset 只负责「把外部数据原样搬到 `storage/raw/`」，彼此无依赖。
structured 层是**第一次出现依赖关系**的地方：它跨源做匹配、消歧、摊分，
产物写到 `storage/structured/`，供 derive / publish 消费。

group_name = `structured_{domain}`（约定 2）。

## ⚠️ 覆盖率和正确率是两个 asset

`structured_plants` 报「匹配上了多少」，`structured_plant_verify` 报「匹配得对不对」。
只跑前者会得到过于乐观的结论 —— 名字对、坐标错**不掉覆盖率也不报错**
（见 LESSONS #23）。所以 verify 是独立 asset 而不是 plants 内部的一个检查，
这样它在 Dagster 上是**可见的、会失败的**一步。

## 依赖关系

    raw_occto_unit ┐
    raw_ajg        ├→ structured_plants ──→ structured_plant_verify
    raw_gem        │
    raw_ksj_p03    ┘

    raw_occto_flow ┐
    raw_ajg        ┘→ structured_lines

    raw_area_jukyu   ┐
    raw_occto_unit   │
    raw_jepoc_hydro  ├→ structured_disaggregated
    raw_gem          │
    raw_ksj_p03      ┘
"""

from dagster import AssetExecutionContext, MaterializeResult, asset

from pipeline.core import paths

from ..resources import PowerjpConfig
from ..utils import run_and_report


@asset(group_name="structured_generation", compute_kind="python",
       deps=["raw_occto_unit", "raw_ajg", "raw_gem", "raw_ksj_p03"])
def structured_plants(context: AssetExecutionContext,
                      cfg: PowerjpConfig) -> MaterializeResult:
    """調度機組 → 坐标。多源合池 + 事业者消歧 + 打分选点 + xref 兜底。

    产物：`storage/structured/plants/plants.json`
    每条带 `coord_source` / `match_method`（auto|xref），供下游区分置信度。
    """
    return run_and_report(context, cfg, "pipeline.structured.plant_coords", "--dump")


@asset(group_name="structured_generation", compute_kind="python",
       deps=["structured_plants"])
def structured_plant_verify(context: AssetExecutionContext,
                            cfg: PowerjpConfig) -> MaterializeResult:
    """空间校验 —— 找「名字对上了但坐标可能错的」。

    ⚠️ 这一步**必须和 plants 一起跑**。覆盖率不掉、不报错的静默错配
    （`広野` 曾取到 716 km 外的同名设施）只有这里能发现。

    产物：`storage/structured/plants/verify.json`
    """
    return run_and_report(context, cfg, "pipeline.structured.plant_verify", "--dump")


@asset(group_name="structured_network", compute_kind="python",
       deps=["raw_occto_flow", "raw_ajg"])
def structured_lines(context: AssetExecutionContext,
                     cfg: PowerjpConfig) -> MaterializeResult:
    """潮流 → 真实走线几何（图上 Dijkstra 求路径）。

    产物：`storage/structured/lines/lines.json`
    每条带 `geometry_source`（现阶段全是 `graph_route`）。
    """
    out = paths.structured("lines") / "lines.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    return run_and_report(context, cfg, "pipeline.structured.flow_route_probe",
                          "--dump", str(out))


@asset(group_name="structured_generation", compute_kind="python",
       deps=["raw_area_jukyu", "raw_occto_unit", "raw_jepoc_hydro",
             "raw_gem", "raw_ksj_p03"])
def structured_disaggregated(context: AssetExecutionContext,
                             cfg: PowerjpConfig) -> MaterializeResult:
    """非调度电源：区域实绩 → 逐站摊分（水力/風力/地熱/バイオマス）。

    ⚠️ 产物里 `data_quality` 区分 `measured` / `estimated`。
    太陽光**有意不做**（区域实绩含住宅屋顶，概念上没有「站」），见 TODO S18。

    产物：`storage/structured/disaggregated/sites.json`
    """
    out = paths.structured("disaggregated") / "sites.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    return run_and_report(context, cfg, "pipeline.structured.disaggregate",
                          "--dump", str(out))
