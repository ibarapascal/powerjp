# powerjp

日本の電力系統（送電網・発電所・基幹送電線の潮流・エリア需給）を、公開データだけで再構成して地図に載せるパイプラインと Web 地図です。

- 地図：<https://powerjp.mkhr.co.jp/>
- 配信データ（静的 JSON / GeoJSON）：<https://data.powerjp.mkhr.co.jp/powerjp/v1/manifest.json>

> 地図は 30 分ごとに更新されています。

## 何をしているか

日本の電力系統については、公開されている情報が意外に多くあります。OCCTO は基幹送電線の潮流実績を 30 分単位で、ユニットごとの発電実績を日次で公表しています。一般送配電事業者 10 社はエリアごとの需給実績を毎時公表しており、送電網の形状は OpenStreetMap にあります。ただし、これらを突き合わせて一枚の地図に重ね、時間軸で追える場所はありませんでした。

`powerjp` は、これらの公開データを取り込み、座標・時刻・エリアを揃え、静的ファイルとして CDN に配信します。Web 地図はそのファイルを読むだけです。

## 地図に載っているもの

| レイヤー | 内容 | 主な出典 |
|---|---|---|
| 送電線・変電所 | 送電線の経路、変電所・開閉所 | All-Japan-Grid（OpenStreetMap 由来） |
| 発電所 | 発電所の位置・電源種・設備容量 | All-Japan-Grid、Global Energy Monitor、国土数値情報 P03 |
| 基幹送電線の潮流 | 30 分ごとの潮流実績を送電線の経路に対応付けたもの | OCCTO 系統情報サービス |
| ユニット別出力 | 火力・原子力・水力ユニットの 30 分ごとの出力実績 | OCCTO ユニット別発電実績公開システム |
| 非調整電源の推計 | 風力・地熱・バイオマスのエリア合計を設備容量比で各発電所に按分した推計値 | エリア需給実績（一般送配電事業者 10 社） |

注意点：

- 潮流の経路は公表された線路名をグラフ上で探索して求めた**推定**で、公式の系統図ではありません。
- `data_quality: estimated` の値は按分による推計で、その地点の実測値ではありません。
- 地図に表示しているのは**観測された潮流**です。潮流計算の結果ではありません（線路インピーダンスの権威ある公開源が無いため）。

## データ源と取得方法

| データ源 | 提供 | 使い方 | 期間・版 | 取得 | スクリプト |
|---|---|---|---|---|---|
| [All-Japan-Grid](https://github.com/lutelute/All-Japan-Grid) | lutelute（OpenStreetMap 由来） | 送電線・変電所・発電所の幾何、経路探索用の拓撲 | GitHub release（v1.7.0） | 自動（GitHub Releases から取得。`--tag` で版指定） | `pipeline/sources/ajg/` |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | OSM contributors | All-Japan-Grid の上流 | — | All-Japan-Grid 経由 | — |
| [系統情報サービス](https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/LOGIN_login)（地内基幹送電線 潮流実績） | 電力広域的運営推進機関（OCCTO） | 基幹送電線の 30 分潮流実績 | 2023-01〜 | 自動（アカウント不要。エリア × 日の CSV） | `pipeline/sources/occto_flow/` |
| [ユニット別発電実績公開システム](https://hatsuden-kokai.occto.or.jp/hks-web-public/) | OCCTO | 火力・原子力・水力ユニットの 30 分出力実績 | 2024-03-25〜（T+1 公表） | 自動（免責事項の同意画面のみ） | `pipeline/sources/occto_unit/` |
| [国土数値情報 発電施設（P03）](https://nlftp.mlit.go.jp/ksj/) | 国土交通省 | 発電所の座標補完（電源種別） | 2013 年度版（P03-13） | 自動 | `pipeline/sources/ksj_p03/` |
| [Global Integrated Power Tracker](https://globalenergymonitor.org/projects/global-integrated-power-tracker/) | Global Energy Monitor | 発電所の座標・設備容量・電源種の補完 | March 2026 release | **手動**（Web フォームで申請してダウンロードし、`storage/raw/gem/{日付}/` に置く） | `pipeline/sources/gem/`（置いたファイルの解析） |
| [エリア需給実績](https://www.occto.or.jp/keitoujouhou/index.html) | 一般送配電事業者 10 社 | 非調整電源（風力・地熱・バイオマス）の按分推計の元データ | 2016 年度〜（統一形式は 2024 年度〜） | 自動（各社サイトの CSV） | `pipeline/sources/area_jukyu/` |
| 底図 | Mapbox | 地図の背景 | — | Mapbox GL JS（要アクセストークン） | `web/` |

- 取得手順（エンドポイント、パラメータ、ファイルの置き場所、既知の落とし穴）は各ディレクトリの `INGEST.md` にあります。
- `pipeline/sources/jepoc_hydro/`（電力土木技術協会 水力発電所データベース）は取得スクリプトのみで、配信物には使っていません（転載に許諾が必要なため）。
- 取得した原データは `storage/raw/{source}/` に日付・版ごとに置き、上書きしません。OCCTO の 2 源は `--from` / `--to` / `--all` で上流から過去分を遡って取得できます。
- 潮流とユニット出力の動的レイヤーは、原データを日次の行形式（スキーマは `pipeline/publish/platform_feed.py` に記載）に整形した中間ファイルを入力にしています。この整形処理は本リポジトリに含まれていません。原データの読み方は `pipeline/structured/` の照合スクリプトにあります。

## 対象外

- 建物単位の需要推計（需要側は 10 エリアまで）
- 配電（6.6 kV フィーダー）の形状（公開されていない）
- 運用レベルの精密シミュレーション

## 構成

```
取得     →  storage/raw/{source}/{date}/           手動 / 半自動 / 自動
──────────────────────── 境界 ────────────────────────
処理     →  structured → derived → publish         決定的・再実行可能
                                       ↓
                          Cloudflare R2  →  Web 地図
```

`storage/raw/` から先はすべて決定的で、いつでも最初から再生成できます。認証やログイン、手動ダウンロードは境界より手前に閉じ込めています。

- 処理：Python（uv）。各スクリプトは純粋な CLI で、定期実行は外部のオーケストレーターが呼び出します
- 保存：PostGIS（幾何）＋ 日付ごとの原ファイル（時系列）
- 配信：Cloudflare R2（ファイル名に内容ハッシュを付け、最後に `manifest.json` を書き換えて原子的に更新）
- 地図：React + Vite + Mapbox GL JS（Cloudflare Pages）

```
pipeline/sources/{name}/   データ源ごとの取得スクリプトと INGEST.md（取得手順・利用条件・既知の落とし穴）
pipeline/structured/       正規化・突合（発電所の名寄せ、潮流と線路の対応付け、按分推計）
pipeline/publish/          配信物の生成と R2 へのアップロード
web/                       Web 地図
docs/                      運用手順（OPERATIONS.md）、設計判断（decisions/）、リリース記録
```

開発用の文書（`CLAUDE.md`、`TODO.md`、`LESSONS.md`、各 `INGEST.md`）は主に中国語で書かれています。

## 使い方

```bash
uv sync
uv run python -m pipeline.sources.occto_flow.ingest --help   # データ源ごとの取得
uv run python -m pipeline.publish.geojson                    # 配信物の生成
cd web && pnpm install && pnpm dev                           # 地図（Mapbox トークンが必要。web/.env.example 参照）
```

デプロイ・更新・ロールバックの手順は [`docs/OPERATIONS.md`](docs/OPERATIONS.md) を参照してください。

## 出典とライセンス

- **コード**：MIT（[`LICENSE`](LICENSE)）
- **配信データ**：OpenStreetMap 由来の幾何を含む派生データベースのため、**ODbL 1.0** で提供します。そのほかのデータ源にはそれぞれの条件があり、出典の表示と、加工した場合はその旨の明記が必要です。データ源ごとの条件は [`ATTRIBUTION.md`](ATTRIBUTION.md) と、地図内の「出典とライセンス」パネルにまとめています。
- このリポジトリにはデータ本体は含まれていません（人手で作成した小さな名寄せ表 `pipeline/structured/*_xref.json` のみ）。

データ源の権利者の方で、掲載内容に問題がある場合は Issue でお知らせください。速やかに対応します。
