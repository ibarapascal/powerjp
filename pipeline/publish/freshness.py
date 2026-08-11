"""检查 publish 快照是否停止更新，并在状态变化时发送 webhook。

这个检查故意不依赖 Dagster：Dagster 本身停掉时，告警仍然要能运行。
默认只看 ``storage/publish/manifest.json`` 的 mtime，不扫描 1.3GB raw，避免
告警任务反而给 私有计算节点 增加 I/O 压力。

配置：

    POWERJP_FRESHNESS_WEBHOOK_URL=...
    POWERJP_FRESHNESS_MAX_AGE_MINUTES=120

webhook 使用常见的 ``{"text": "..."}`` payload（Slack/Discord 的 incoming
webhook 均可按各自网关适配）。没有配置 webhook 时仍会在日志里报 stale 并以
退出码 2 结束，便于之后接入其他通知渠道。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from pipeline.core import paths

log = logging.getLogger("powerjp.publish.freshness")


def _state_path(manifest: Path) -> Path:
    return manifest.parent.parent / ".powerjp-freshness.json"


def _read_state(path: Path) -> str | None:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return data.get("status") if isinstance(data, dict) else None


def _write_state(path: Path, status: str) -> None:
    path.write_text(
        json.dumps({"status": status, "checked_at": int(time.time())}, ensure_ascii=False),
        encoding="utf-8",
    )


def _notify(url: str, text: str) -> None:
    response = httpx.post(url, json={"text": text}, timeout=10.0)
    response.raise_for_status()


def check(
    manifest: Path,
    *,
    max_age_minutes: int,
    webhook_url: str | None,
    state_file: Path | None = None,
    dry_run: bool = False,
) -> int:
    now = time.time()
    try:
        mtime = manifest.stat().st_mtime
    except FileNotFoundError:
        age_minutes = None
    else:
        age_minutes = max(0.0, (now - mtime) / 60)

    stale = age_minutes is None or age_minutes > max_age_minutes
    status = "stale" if stale else "fresh"
    state_path = state_file or _state_path(manifest)
    previous = _read_state(state_path)

    if stale:
        age_text = (
            "manifest.json 不存在"
            if age_minutes is None
            else f"已 {age_minutes:.0f} 分钟未更新"
        )
        message = f"[powerjp] publish 数据过期：{age_text}（阈值 {max_age_minutes} 分钟）"
        log.error("%s: %s", message, manifest)
    else:
        message = (
            f"[powerjp] publish 数据已恢复：{age_minutes:.0f} 分钟前更新"
            if previous == "stale"
            else f"[powerjp] publish 数据正常：{age_minutes:.0f} 分钟前更新"
        )
        log.info("%s", message)

    changed = previous != status
    if changed and webhook_url and not dry_run:
        try:
            _notify(webhook_url, message)
        except httpx.HTTPError as exc:
            log.error("发送 freshness webhook 失败：%s", exc)
            return 2 if stale else 1
    elif changed and webhook_url and dry_run:
        log.info("dry-run：会发送 freshness webhook")
    elif stale and not webhook_url:
        log.error("未配置 POWERJP_FRESHNESS_WEBHOOK_URL，只有日志/退出码告警")

    # webhook 失败时不记状态，让下一次巡检继续重试；没有 webhook 时也不把
    # stale 记住，以便后来补上通知配置后能收到第一次过期告警。
    if not dry_run and (not stale or webhook_url):
        _write_state(state_path, status)

    return 2 if stale else 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=paths.storage() / "publish" / "manifest.json",
    )
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=int(os.environ.get("POWERJP_FRESHNESS_MAX_AGE_MINUTES", "120")),
    )
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("POWERJP_FRESHNESS_WEBHOOK_URL", "").strip() or None,
    )
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.max_age_minutes <= 0:
        parser.error("--max-age-minutes 必须为正数")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return check(
        args.manifest,
        max_age_minutes=args.max_age_minutes,
        webhook_url=args.webhook_url,
        state_file=args.state_file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
