# powerjp web

日本の電力系統の可視化フロントエンド。React + Vite + Mapbox GL JS。

## 起動

```bash
cd web
pnpm install
cp .env.example .env      # VITE_MAPBOX_TOKEN を記入
pnpm dev                  # http://localhost:8081
```

⚠️ **`.env` を編集したら dev サーバーを再起動すること**（Vite は env を起動時にしか読まない）。

## SEO / GEO

`index.html` と `public/` に、単一ページの公開前提で必要な発見性 metadata をまとめている：

- `favicon.svg`、`manifest.json`、dark theme metadata
- Japanese title/description、canonical、Open Graph、Twitter Card
- `geo.region=JP` / `geo.placename=Japan`（全国データなので都市の架空座標は付けない）
- WebSite / Organization / WebApplication / Dataset の JSON-LD
- `robots.txt`、`sitemap.xml`
- `/.well-known/ai.json`、`ai.html`、`llms.txt`、`llms-full.txt`

Static metadata points to the production URL `https://powerjp.mkhr.co.jp/`.
Preview deployments use a separate Pages URL; keep the production canonical URL in
`index.html`, `public/`, `AI.md`, and the sitemap. Preview deployments should be
protected with Cloudflare Access and are expected to remain non-indexed.

Frontend-only changes are released by building `web/` and deploying `web/dist`
to Cloudflare Pages; they do not require the private compute machine or a
Dagster restart. The complete production command and post-deploy smoke checks live in
[`docs/OPERATIONS.md`](../docs/OPERATIONS.md).

## データはどこから来るか

```
storage/publish/*.geojson                 ← pipeline の publish 層が生成
        ↓ dev: vite プラグインが /data で配信（vite.config.ts）
        ↓ 本番: R2 → data.powerjp.mkhr.co.jp
      manifest.json → content-hash object → fetch()
```

**`public/` には置かない** —— そこに置くと build 時に 10 MB が dist にコピーされる。
本番のデータは CDN 側に置き、`dist` にはコードだけを含める。

前端は `manifest.json` を 5 分ごとに检查し、manifest 变更的对象才会重新读取；这不是整页
刷新，用户当前的缩放、图层和时间滑块状态会保留。

## R2 に公開する

R2 の認証情報は repo 直下の `.env` にだけ置く（`.env.example` 参照）。初回は
全層を確認してから公開し、その後の 30 分更新は dynamic 層だけを送る：

```bash
uv run python -m pipeline.publish.upload --scope all --dry-run
uv run python -m pipeline.publish.upload --scope all
uv run python -m pipeline.publish.upload --scope dynamic
```

アップロード対象の定義は `pipeline/publish/spec.py` が唯一の出所。データ对象名
带内容 hash，`manifest.json` 最后写入；生产环境的 `VITE_DATA_BASE` 必须指向
`R2_PREFIX` 对应的 CDN 路径。每次 manifest 成功更新后，默认会清理 R2 上不再被当前
manifest 引用、且超过 `R2_PRUNE_GRACE_HOURS`（默认 6 小时）的旧 hash 对象；私有计算节点
上的 raw 历史和本地 publish 重建能力不受影响。排障时可用 `--no-prune` 暂停清理。

データを更新するには、リポジトリのルートで：

```bash
uv run python -m pipeline.publish.geojson
```

`storage/structured/` の成果物から `storage/publish/*.geojson` を作り直す。

## ★ レイヤーを追加する

**`src/layers.ts` の `LAYERS` に 1 項目足すだけ。** 他のファイルは触らなくてよい。
サイドバー・凡例・ON/OFF・クリック時の情報パネルは全部この宣言から生成される。

```ts
{
  id: 'my_layer',
  group: '潮流',              // GROUP_ORDER にある名前
  label: '新しいレイヤー',
  file: 'my_layer.geojson',   // storage/publish/ 配下のファイル名
  kind: 'line',               // 'line' | 'circle'
  defaultOn: false,
  note: '実測か推算かをここに明記する',
  paint: { 'line-color': '#fff' },
  legend: [{ color: '#fff', label: '説明' }],
  popupFields: [{ key: 'name', label: '名称' }],
}
```

## ⚠️ 守るべきこと

### データ品質を視覚的に区別する

| 種別 | 意味 | 表現 |
|---|---|---|
| `measured` | OCCTO の 30 分実測 | 塗りつぶし + 白フチ |
| `estimated` | エリア実績を設備容量比で按分した**推算** | 中空の円 |
| `geometry_source: graph_route` | グラフ上で求めた経路。**公式の線路図ではない** | 情報パネルに明記 |

**推算値と実測値を同じ見た目で描くのは嘘をつくのと同じ。**

### 幾何は簡略化しない

Douglas-Peucker もズームレベル別の間引きもしない（`CLAUDE.md` の publish 層ハード制約）。
`grid_lines` は 7.7 MB（gzip 後 約 2 MB）あるが、**それは仕様**。
軽くしたい場合は「簡略化」ではなく**電圧別にファイルを分けて遅延読込**する。

### PMTiles は使えない

`addProtocol` は MapLibre の API で Mapbox GL JS には無い（`LESSONS.md` #2）。
タイル化するなら `{z}/{x}/{y}.pbf` のディレクトリ形式。

## 構成

| ファイル | 役割 |
|---|---|
| `src/layers.ts` | **レイヤー定義。拡張はここだけ** |
| `src/slots.ts` | `slots_mw` を式から引く際の唯一の出処。⛔ `['at', i, ['get','slots_mw']]` と直接書くと欠測 null で全コマ描画不能（`LESSONS.md` #42） |
| `src/MapView.tsx` | Mapbox のラッパ。宣言からレイヤーを動的追加、遅延読込 |
| `src/Sidebar.tsx` | 左サイドバー。中身は `layers.ts` から生成 |
| `src/FeaturePanel.tsx` | 要素クリック時の情報パネル |
| `vite.config.ts` | dev 用のデータ配信プラグインを含む |
