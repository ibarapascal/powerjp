"""Dagster Definitions 入口。

起动：
    uv run dagster dev -m orchestration.definitions -h 127.0.0.1 -p 3001

⚠️ 不要用 `dagster-webserver` —— 它没有 daemon，run 会卡在 QUEUED（LESSONS.md #1）。
端口默认 3001（可用 -p 改）。
"""

from __future__ import annotations

from dagster import Definitions, load_assets_from_modules

from .assets import publish, raw, structured
from .resources import PowerjpConfig
from .schedules import JOBS, SCHEDULES

defs = Definitions(
    assets=load_assets_from_modules([raw, structured, publish]),
    jobs=JOBS,
    schedules=SCHEDULES,
    resources={"cfg": PowerjpConfig()},
)
