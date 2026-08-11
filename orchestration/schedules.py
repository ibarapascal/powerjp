"""定時実行。**30 分更新**と**日次の穴埋め**の 2 本立て。

## なぜ 2 本要るのか

OCCTO は 30 分ごとに更新するが、**当日ぶんは 23:32 を過ぎるまで揃わない**。
一方 ingest は「ファイルが在れば飛ばす」作りなので、当日ファイルを一度
取ってしまうと放っておいても更新されない。

    30 分更新   直近 2 日を force で上書き    ← 常に最新断面を映すため
    日次穴埋め  直近 7 日を force なしで補完  ← 落ちた回・欠けた日を拾うため

`days=2` は日付をまたぐ瞬間の取りこぼし防止（当日＋前日）。

## 幾何は回さない

30 分ジョブは ingest 2 つと publish だけ。`structured_lines` の経路探索は
数分かかるうえ、送電線の走りは 30 分では変わらない。

## ⚠️ タイムゾーン

OCCTO の時刻は日本時間。`execution_timezone` を省くと UTC 基準になり、
「当日」の境目が 9 時間ずれる。
"""

from dagster import (
    AssetSelection, RunConfig, ScheduleDefinition, define_asset_job,
    DefaultScheduleStatus,
)

from pipeline.publish import spec

from .assets.publish import PublishScope
from .assets.raw import IngestWindow

TZ = "Asia/Tokyo"
# 層の名前も spec 由来。文字列を直に書かない（打ち間違えても実行時まで気づけない）
DYNAMIC = "dynamic"
assert DYNAMIC in spec.LAYER_FILES, f"{DYNAMIC} は spec に無い層"

# ── 30 分更新 ────────────────────────────────────────────────
# 直近 2 日を force で取り直し、publish まで通す。
_REFRESH = AssetSelection.assets(
    "raw_occto_flow", "raw_occto_unit", "publish_geojson", "publish_r2",
)

refresh_job = define_asset_job(
    name="refresh_30min",
    selection=_REFRESH,
    description="潮流・機組の直近 2 日を取り直して GeoJSON を作り直す",
    config=RunConfig(ops={
        # ★ force=True でないと当日ファイルが更新されない（IngestWindow の注記）
        "raw_occto_flow": IngestWindow(days=2, force=True),
        "raw_occto_unit": IngestWindow(days=2, force=True),
        # ★ static（送電線 19.6MB・変電所）は 30 分では変わらない。作り直さない
        "publish_geojson": PublishScope(only=DYNAMIC),
        "publish_r2": PublishScope(only=DYNAMIC),
    }),
)

# ⚠️ OCCTO 側に約 2 分の時滞があるので、毎時 00/30 ちょうどではなく
#    05/35 に取りに行く。ちょうどに行くと最新コマが空で返ることがある。
refresh_schedule = ScheduleDefinition(
    name="refresh_30min_schedule",
    job=refresh_job,
    cron_schedule="5,35 * * * *",
    execution_timezone=TZ,
    default_status=DefaultScheduleStatus.RUNNING,
)

# ── 日次の穴埋め ──────────────────────────────────────────────
# force を付けない＝既に揃っている日は触らない。落ちた回の取りこぼしを拾う。
backfill_job = define_asset_job(
    name="backfill_daily",
    selection=_REFRESH,
    description="直近 7 日の抜けを補完してから GeoJSON を作り直す",
    config=RunConfig(ops={
        "raw_occto_flow": IngestWindow(days=7, force=False),
        "raw_occto_unit": IngestWindow(days=7, force=False),
        "publish_geojson": PublishScope(only=DYNAMIC),
        "publish_r2": PublishScope(only=DYNAMIC),
    }),
)

# 当日ぶんが揃うのは 23:32 以降。日付が変わってから前日を確定させる
backfill_schedule = ScheduleDefinition(
    name="backfill_daily_schedule",
    job=backfill_job,
    cron_schedule="20 1 * * *",
    execution_timezone=TZ,
    default_status=DefaultScheduleStatus.RUNNING,
)

SCHEDULES = [refresh_schedule, backfill_schedule]
JOBS = [refresh_job, backfill_job]
