"""mkhr-data 平台 publish 面（publish/v1）の入力アダプタ。

2026-08-27 owner 裁决：dynamic 層（flow_lines / flow_stamps / plants）は raw CSV/JSON
直読をやめ、平台の発布面を食う——確定日＝ `records/{ISO日}.jsonl.gz`、当日＝
`current.jsonl.gz`（30 分班 :05/:35 が再構築、本链 :10/:40 が消費）。R2 と逐字节同物の
ローカル樹を読むので往復なし。structured 層（手動再計算）は従来どおり raw 直読で、
本モジュールの範囲外。

行スキーマ（平台 ingestion/occto_{flow,unit}/parse.py が SSOT）：
  flow: {date, area, area_code, voltage_kv, line_name, direction, values[48]}
  unit: {plant_code, area_code, area, plant_name, unit_name, fuel, date,
         values[48], day_total, updated_at}
values[i] は**コマ始点** 00:00+30i の値（unit は平台側で終点キー htdnRslt0030… を
始点制に並べ替え済み——raw 直読時代の「30 分引く」補正は不要）。
"""

import gzip
import json
import os
from pathlib import Path


def publish_root() -> Path:
    """平台 publish 樹のルート。計算ノードでは /Volumes/fast/mkhr-data/publish。"""
    env = os.environ.get("MKHR_PUBLISH_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path("/Volumes/fast/mkhr-data/publish")


def _iso(day_compact: str) -> str:
    return f"{day_compact[:4]}-{day_compact[4:6]}-{day_compact[6:]}"


def _read_gz_jsonl(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def available_days(source: str, today_compact: str) -> list[str]:
    """データが存在する日（YYYYMMDD、昇順）：records の確定日 ＋ current があれば当日。"""
    root = publish_root() / "v1" / source
    days = sorted(p.name[:10].replace("-", "")
                  for p in (root / "records").glob("*.jsonl.gz"))
    if (root / "current.jsonl.gz").is_file() and today_compact not in days:
        days.append(today_compact)
    return days


def iter_rows(source: str, days_compact: list[str], today_compact: str):
    """(day_compact, row) を対象日ぶん yield。当日は current、それ以外は records。

    records（≤昨日）と current（当日）は日で互いに素——重複読みは起きない。
    対象日のファイルが無ければ黙って欠測扱い（窓の途中の欠測はデータの事実）。
    """
    root = publish_root() / "v1" / source
    for dc in days_compact:
        path = (root / "current.jsonl.gz" if dc == today_compact
                else root / "records" / f"{_iso(dc)}.jsonl.gz")
        if not path.is_file():
            continue
        for row in _read_gz_jsonl(path):
            yield dc, row
