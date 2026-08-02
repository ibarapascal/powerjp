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

## データはどこから来るか

```
storage/publish/*.geojson      ← pipeline の publish 層が生成
        ↓ dev: vite プラグインが /data で配信（vite.config.ts）
        ↓ 本番: R2 → CDN、VITE_DATA_BASE で指す
      fetch('/data/xxx.geojson')
```

**`public/` には置かない** —— そこに置くと build 時に 10 MB が dist にコピーされる。
本番のデータは CDN 側に置き、`dist` にはコードだけを含める。

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
| `src/MapView.tsx` | Mapbox のラッパ。宣言からレイヤーを動的追加、遅延読込 |
| `src/Sidebar.tsx` | 左サイドバー。中身は `layers.ts` から生成 |
| `src/FeaturePanel.tsx` | 要素クリック時の情報パネル |
| `vite.config.ts` | dev 用のデータ配信プラグインを含む |
