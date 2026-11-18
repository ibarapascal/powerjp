"""外部データ平台の publish 面（publish/v1）の入力アダプタ。

2026-08-27 owner 裁决：dynamic 層（flow_lines / flow_stamps / plants）は raw CSV/JSON
直読をやめ、平台の発布面を食う——確定日＝ `records/{ISO日}.jsonl.gz`、最新断面＝
`latest.jsonl.gz`（2026-08-29 改形：旧 `current.jsonl.gz`。**日の枠組みを持たない**
「源発布の最新鏡像」で、日別の組み立ては行の `date` を見て消費側＝ここがやる）。R2 と逐字节同物のローカル樹を読むので
往復なし。structured 層（手動再計算）は従来どおり raw 直読で、本モジュールの範囲外。

行スキーマ（平台側の parse が SSOT）：
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
    """平台 publish 樹のルート。環境変数 MKHR_PUBLISH_ROOT で指定する。"""
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


def _latest_rows_by_day(source: str) -> dict[str, list[dict]]:
    """latest.jsonl.gz を行の `date` で日別（YYYYMMDD）に分ける。

    ファイル名は日を主張しない——日跨ぎ直後は前日の内容が残っていることがあり、
    「ファイルが在る＝当日ぶんが在る」ではない。date で分ければその残留も正しい
    日に落ちる（旧 current 時代の「今日」誤標の是正）。ファイルが無ければ空 dict。
    """
    path = publish_root() / "v1" / source / "latest.jsonl.gz"
    if not path.is_file():
        return {}
    out: dict[str, list[dict]] = {}
    for row in _read_gz_jsonl(path):
        dc = (row.get("date") or "").replace("/", "").replace("-", "")
        if len(dc) == 8:
            out.setdefault(dc, []).append(row)
    return out


def available_days(source: str) -> list[str]:
    """データが存在する日（YYYYMMDD、昇順）：records の確定日 ＋ latest にある日。"""
    root = publish_root() / "v1" / source
    days = sorted(p.name[:10].replace("-", "")
                  for p in (root / "records").glob("*.jsonl.gz"))
    have = set(days)
    extra = [dc for dc in _latest_rows_by_day(source) if dc not in have]
    return sorted(days + extra)


def iter_rows(source: str, days_compact: list[str]):
    """(day_compact, row) を対象日ぶん yield。確定日は records、無い日は latest の該当日。

    records を優先（確定件が真源）。latest は records に無い日だけ読むので重複しない。
    対象日のファイル/行が無ければ黙って欠測扱い（窓の途中の欠測はデータの事実）。
    """
    root = publish_root() / "v1" / source
    latest_by_day: dict[str, list[dict]] | None = None
    for dc in days_compact:
        rec = root / "records" / f"{_iso(dc)}.jsonl.gz"
        if rec.is_file():
            for row in _read_gz_jsonl(rec):
                yield dc, row
            continue
        if latest_by_day is None:
            latest_by_day = _latest_rows_by_day(source)
        for row in latest_by_day.get(dc, []):
            yield dc, row
