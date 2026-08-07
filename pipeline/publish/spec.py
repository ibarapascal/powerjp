"""publish 層の**唯一の定義**。どのファイルがどの層に属するか。

## ⚠️ ここが SSOT。同じ一覧を他所に書かない

この定義は少なくとも 4 か所から参照される：

    pipeline/publish/geojson.py          どれを作り直すか
    orchestration/assets/publish.py      Dagster の config の選択肢
    orchestration/schedules.py           30 分ジョブが指定する層
    （将来）配信スクリプト                 キャッシュ制御を層ごとに変える

一覧をそれぞれに書くと、ファイルを 1 つ足したときに**どこかが古いまま**になる。
そして「新しいファイルが CDN に上がらない」のような、原因から遠い症状で出る。

## 層の分け方は「更新頻度」であって「種類」ではない

| 層 | 中身 | 変わるとき | 配信 |
|---|---|---|---|
| `static` | 送電線・変電所・非調整電源 | AJG が新版を出したとき（年に数回） | 長期キャッシュ |
| `dynamic` | 潮流・機組・コマ時刻 | 30 分ごと | 短期キャッシュ |

30 分の更新で static まで作り直すと、19.6MB の `grid_lines` を毎回書き直し、
変電所 8,994 点の種別判定も毎回やることになる。配信側でも 1 日 940MB を無駄に流す。

## ⚠️ publish は**派生物**であって原本ではない

ここの成果物は全部 `storage/raw/` から作り直せる。消えても困らない。
**保存しておくべきは raw**（日付ごとに落ちていて上書きされない）。
分析用の時系列は publish ではなく別途 Parquet に積む（TODO S4〜S7）。
"""

from __future__ import annotations

# 層 → その層に属するファイル名。★ 追加はここだけ
LAYER_FILES: dict[str, list[str]] = {
    "static": [
        "grid_lines.geojson",
        "substations.geojson",
        "sites.geojson",
    ],
    "dynamic": [
        "flow_lines.geojson",
        "flow_stamps.json",
        "plants.geojson",
    ],
}

# `--only` / Dagster config が受け取る値。`all` は両方
# ★ 層に属さないが**必ず一緒に配る**もの。
#
# ⚠️ `LICENSE.txt` を落とさないこと。配信している GeoJSON は OSM 由来の
#    幾何を含む派生データベースなので ODbL の対象で、**許諾と出典を成果物に
#    添えるのは義務**。前端の画面にだけ書いても、GeoJSON を直接取得した人には
#    届かない。
# ⚠️ `manifest.json` は前端が鮮度を判定するのに使う（`web/src/manifest.ts`）。
#    これが無いと dynamic の更新に追従できない。
ALWAYS_FILES: list[str] = [
    "manifest.json",
    "LICENSE.txt",
]

SCOPES = ["all", *LAYER_FILES]


def files_for(scope: str) -> list[str]:
    """scope に含まれるファイル名。"""
    if scope == "all":
        return [f for names in LAYER_FILES.values() for f in names]
    return list(LAYER_FILES[scope])


def upload_files(scope: str = "all") -> list[str]:
    """配信スクリプトが**実際に上げるべき**ファイル一覧。

    層のファイル + `ALWAYS_FILES`。⚠️ アップロード側でこの一覧を
    書き直さないこと —— 片方だけ直すと LICENSE が落ちる。
    ⚠️ `manifest.json` は**最後に**上げる（`CLAUDE.md` 約定 8 の発行順）。
    """
    return [*files_for(scope), *[f for f in ALWAYS_FILES if f != "manifest.json"],
            "manifest.json"]


def layer_of(filename: str) -> str | None:
    """ファイル名 → 層。配信スクリプトがキャッシュ制御を決めるのに使う。"""
    for layer, names in LAYER_FILES.items():
        if filename in names:
            return layer
    return None
