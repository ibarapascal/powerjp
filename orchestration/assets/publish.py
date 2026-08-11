"""publish 层 assets —— structured 产物 → 前端が直接読む GeoJSON。

## この層だけ 30 分ごとに回る

幾何（送電線の走り・変電所の位置・経路探索の結果）は**時間で変わらない**。
変わるのは値だけ：潮流 464 本 × 30 分、機組 183 台 × 30 分。

だから 30 分の更新で回すのは

    raw_occto_flow ┐
    raw_occto_unit ┴→ publish_geojson → publish_r2

の 4 asset だけでよい。`structured_lines`（Dijkstra の経路探索）は
数分かかるうえ結果が変わらないので**入れない**。

## ⚠️ 依存に structured を書いてあるが、30 分ジョブでは選択しない

`deps` は「どちらが先か」の宣言であって「毎回一緒に回す」意味ではない。
ジョブ側の `selection` で publish だけを選ぶ。
"""

from dagster import AssetExecutionContext, Config, MaterializeResult, asset

from pipeline.publish import spec

from ..resources import PowerjpConfig
from ..utils import run_and_report


class PublishScope(Config):
    """作り直す層。

    ⚠️ 選べる値と各層の中身は `pipeline.publish.spec` が唯一の出所。
       ここに一覧を書き写さない —— ファイルを 1 つ足したときに片方だけ
       古くなる。

    30 分ごとに `all` を回すと、19.6MB の `grid_lines` を毎回書き直し、
    変電所 8,994 点の種別判定も毎回やる。**どちらも 30 分では変わらない。**
    """

    only: str = "all"

    def validated(self) -> str:
        if self.only not in spec.SCOPES:
            raise ValueError(
                f"only={self.only!r} は不正。選べるのは {spec.SCOPES}")
        return self.only


@asset(group_name="publish", compute_kind="python",
       deps=["structured_lines", "structured_plants", "structured_disaggregated",
             "structured_substation_type", "raw_occto_flow", "raw_occto_unit"])
def publish_geojson(context: AssetExecutionContext, cfg: PowerjpConfig,
                    config: PublishScope) -> MaterializeResult:
    """`storage/publish/*.geojson` を作り直す。

    出力：
    - `grid_lines.geojson` 送電線 40,924（原データ由来。拓撲図ではない）
    - `substations.geojson` 変電所ほか 8,994（`stype` で種別を区別）
    - `flow_lines.geojson` 潮流 684 区間 × **240 コマ**（直近 120 時間）
    - `flow_stamps.json` コマの時刻ラベル（全 feature 共通なので別ファイル）
    - `plants.geojson` 調整力機組 183（潮流と**同じ時間軸**の `slots_mw`）
    - `sites.geojson` 非調整電源（许可过滤后）

    ⚠️ 幾何は簡略化しない（CLAUDE.md publish 層ハード制約）。
    """
    return run_and_report(context, cfg, "pipeline.publish.geojson",
                          "--only", config.validated())


@asset(group_name="publish", compute_kind="upload", deps=["publish_geojson"])
def publish_r2(context: AssetExecutionContext, cfg: PowerjpConfig,
               config: PublishScope) -> MaterializeResult:
    """把 publish 产物以哈希对象发布到 R2，manifest 最后更新。

    30 分钟 job 只传 dynamic；首次上线或静态数据变化时手动选择 all。
    上传脚本自身还会用文件锁防止两个 run 同时改同一个 manifest。
    """
    return run_and_report(context, cfg, "pipeline.publish.upload",
                          "--scope", config.validated())
