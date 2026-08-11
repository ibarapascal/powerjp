"""raw 层 assets —— Ingest / Process 分界线的左边。

asset 名只用 ASCII，可读性靠 docstring（CLAUDE.md 约定 1）。
group_name = {layer}_{domain}（约定 2）。
"""

from dagster import AssetExecutionContext, Config, MaterializeResult, asset

from ..resources import PowerjpConfig
from ..utils import report_manual, run_and_report


class IngestWindow(Config):
    """時系列 ingest の取得窓。**30 分ごとの更新と日次の穴埋めで値が違う。**

    ## ⚠️ なぜ `force` が要るのか

    ingest は「ファイルが在って空でなければ飛ばす」。回填を何度でも安全に
    再開できるようにするための作りだが、**当日ぶんには都合が悪い**：
    00:30 に取った当日ファイルは 01:00 の断面を含まないのに、
    2 回目以降は「もう在る」と判断されて**永久に更新されない**。

    だから 30 分更新は `force=True` で上書きさせる。
    日次の穴埋めは `force=False` のまま、揃っている日を触らない。

    `include_today=True` を付けた `days=2` は日付をまたぐ瞬間の取りこぼしを
    防ぐため（当日＋前日）。通常の CLI の `--days 2` は昨日までの 2 日間。
    """

    days: int = 7
    force: bool = False
    include_today: bool = False

    def args(self) -> list[str]:
        a = ["--days", str(self.days)]
        if self.force:
            a.append("--force")
        if self.include_today:
            a.append("--include-today")
        return a


# ---------------------------------------------------------------- 网侧几何


@asset(group_name="raw_network", compute_kind="download")
def raw_ajg(context: AssetExecutionContext, cfg: PowerjpConfig) -> MaterializeResult:
    """All-Japan-Grid 送电网几何（送电线 / 变电所 / 发电所、WGS84）。

    源码 tarball（主源 30 个区域 GeoJSON）+ release bundle（配信层与拓扑正典）。
    ODbL —— 衍生数据库需同样开放。
    """
    return run_and_report(context, cfg, "pipeline.sources.ajg.ingest")


@asset(group_name="raw_network", compute_kind="download")
def raw_occto_flow(
    context: AssetExecutionContext, cfg: PowerjpConfig, config: IngestWindow
) -> MaterializeResult:
    """OCCTO 系統情報サービス 地内基幹送電線潮流実績（逐线、30 分 × 48 断面/日）。

    **不需要账号** —— 公表部分是匿名身份，POST 一次 LOGIN 拿 session 即可。
    默认只取最近 7 天增量；历史回填用 CLI 的 --all 单独跑。
    """
    return run_and_report(
        context, cfg, "pipeline.sources.occto_flow.ingest", *config.args()
    )


# ---------------------------------------------------------------- 时序


@asset(group_name="raw_timeseries", compute_kind="download")
def raw_area_jukyu(
    context: AssetExecutionContext, cfg: PowerjpConfig
) -> MaterializeResult:
    """エリア需給実績 10 社（逐时，含电源种别分项）。

    FY2024 以降是全国统一命名 `eria_jukyu_{YYYYMM}_{NN}.csv`，
    FY2023 以前各社各不相同。详见 INGEST.md。
    """
    return run_and_report(context, cfg, "pipeline.sources.area_jukyu.ingest")


@asset(group_name="raw_generation", compute_kind="download")
def raw_occto_unit(
    context: AssetExecutionContext, cfg: PowerjpConfig, config: IngestWindow
) -> MaterializeResult:
    """OCCTO ユニット別発電実績（逐机组 × 30 分 × 48 断面/日）。

    唯一的机组级出力源。2024-03-25 起，每日 15:30 更新。
    默认只取最近 7 天增量；历史回填用 CLI 的 --all 单独跑。
    """
    return run_and_report(
        context, cfg, "pipeline.sources.occto_unit.ingest", *config.args()
    )


# ---------------------------------------------------------------- 発電資産


@asset(group_name="raw_generation", compute_kind="manual")
def raw_gem(context: AssetExecutionContext, cfg: PowerjpConfig) -> MaterializeResult:
    """GEM Global Integrated Power Tracker（机组级属性 + **经纬度**）。

    日本 12,074 行 52 列，100% 带坐标，另有 `Plant name (local)` 日文原名。
    **坐标源里唯一对 AJG 有增量的**（+5.0pt）。

    manual：下载在 gated 表单后（name/email/organization/sector/country/
    use≥100字/license），人工提交后把 xlsx 放进 storage/raw/gem/{YYYY-MM-DD}/。
    脚本只做扫描与汇总。
    """
    return run_and_report(context, cfg, "pipeline.sources.gem.ingest")


@asset(group_name="raw_generation", compute_kind="download")
def raw_ksj_p03(
    context: AssetExecutionContext, cfg: PowerjpConfig
) -> MaterializeResult:
    """国土数値情報 発電施設 P03（按电源种分层的点数据，**带坐标**）。

    12,814 条 / 9 层：揚水 46・池 92・一般水力 698・風力 417・太陽光 10,654 等。
    2013 年度冻结 —— 对长寿命资产可用，对新建设备覆盖不足。
    """
    return run_and_report(context, cfg, "pipeline.sources.ksj_p03.ingest")


@asset(group_name="raw_generation", compute_kind="download")
def raw_jepoc_hydro(
    context: AssetExecutionContext, cfg: PowerjpConfig
) -> MaterializeResult:
    """JEPOC 水力発電所データベース（**建模参数**，无坐标）。

    1,801 座。発電形式 / 発電方式（流込み式・調整池式・貯水池式・揚水）/
    有効落差 / 最大使用水量 / 流域面積 —— AJG 与 GEM 都没有的水力参数。
    """
    return run_and_report(context, cfg, "pipeline.sources.jepoc_hydro.ingest")

