/**
 * 图层注册表 —— 这是前端唯一需要改的扩展点。
 *
 * 加一个新图层 = 在 LAYERS 里加一项。侧边栏、图例、开关、点击信息面板
 * 全部由这份声明驱动，不需要改其它文件。
 *
 * ## ⚠️ data_quality 必须在视觉上区分得出来
 *
 * - `measured`  = OCCTO 逐机组实测
 * - `estimated` = 按装机容量从区域实绩摊分出来的推算值
 * - 潮流线的 `geometry_source=graph_route` 是图上求解的路径，**不是官方走线图**
 *
 * 把推算值和实测值渲染成一样的东西是在骗人。推算用**空心/虚线**，实测用实心。
 *
 * ## ⛔ 密度を理由に要素を隠さない
 *
 * ユーザー方針（2026-08-02）：**「太密没事，放大能看就行，不要缺东西」**。
 *
 * - `minzoom` で低ズーム時に消す、といった間引きを**しない**
 * - 全国表示で点が重なっても、それは想定内。寄れば分離して見える
 * - 「見づらい」への対処は**削ること**ではなく、色・不透明度・重ね順で解く
 *
 * これは `CLAUDE.md` の「幾何を簡略化しない」と同じ原則
 * ——**保真を可読性より優先する**——の UI 側の現れ。
 */

export type LegendItem = { color: string; label: string; dashed?: boolean }

export type LayerDef = {
  id: string
  group: string
  label: string
  /** 相对 public/data/ 的文件名 */
  file: string
  kind: 'line' | 'circle'
  /** mapbox paint 表达式 */
  paint: Record<string, unknown>
  defaultOn: boolean
  /** 侧边栏里的一句话说明 —— 尤其要写清是实测还是推算 */
  note?: string
  legend?: LegendItem[]
  /** 点击要素时，信息面板按这个顺序显示属性 */
  popupFields?: { key: string; label: string }[]
  minzoom?: number
  /** 重ね順（小さいほど下）。未指定は配列順 */
  order?: number
}

/**
 * 電圧の配色：**青系のみ**で、明度で段階を作る。
 * 暗い底図の上では「濃い青」は沈むので、高電圧ほど**明るく**する。
 * 潮流（黄→橙→赤）と色相で完全に分離するのが狙い。
 */
const KV_COLOR: unknown[] = [
  'step',
  ['coalesce', ['get', 'kv'], 0],
  '#3f4c5e',      // <66kV / 不明 —— 青灰
  66, '#4a7099',  // 66–153   —— 青
  154, '#4f93cc', // 154–274  —— 浅青
  275, '#3fa9e8', // 275–499  —— 鮮やかな青
  500, '#29c3ff', // ≥500     —— シアン寄りの明るい青（最も目立つ）
]

// ⚠️ 最高圧を白に近づけない（#d6ecff / #9ccbf0 は失敗）。
//    潮流の橙線に隣接・並走したとき「潮流に白フチが付いている」ように見える。
//    明度ではなく**彩度**で目立たせること。青系から出ないこと。

/**
 * ★ 電圧ごとの線幅。**これがただ一つの定義**。
 *
 * 送電線・潮流・流光はすべてこの式を参照する。
 * 「潮流の重畳は色だけを変え、太さは変えない」（ユーザー方針）を
 * コードの構造として保証するため、潮流側に別の幅を書けないようにしている。
 *
 * ⚠️ 潮流を太くしてはいけない。太くすると高電圧線で流光が埋もれ、
 *    細い線の潮流の方が読みやすいという逆転が起きる（実際にそうなった）。
 */
export const KV_LINE_WIDTH: unknown[] = [
  'interpolate', ['linear'], ['zoom'],
  // ⛔ この数値を勝手に動かさないこと。潮流・流光もこの式を共有しており、
  //    ここを触ると「潮流と青線で段が揃わない」という不整合になる。
  //    段の見え方を変えたい場合でも、変えていいのはこの 1 か所だけ。
  4, ['step', ['coalesce', ['get', 'kv'], 0], 0.6, 66, 1.0, 154, 1.5, 275, 2.2, 500, 3.2],
  11, ['step', ['coalesce', ['get', 'kv'], 0], 1.6, 66, 2.4, 154, 3.6, 275, 5.2, 500, 7.4],
]

/**
 * ⚠️ 流光の幅は**ここでは定義しない**。
 *
 * `line-dasharray` は線幅ごとに dash テクスチャを作る実装なので、
 * data-driven な幅（['step', ['get','kv'], …]）と併用すると
 * 1 レイヤーに複数の幅が混ざり、毎フレームのテクスチャ再生成で
 * **一部の線だけが明滅する**。
 *
 * そのため流光は `MapView.tsx` の `GLOW_TIERS` で電圧帯ごとに
 * レイヤーを分け、各帯で幅を定数にしている。
 * KV_LINE_WIDTH の数値を変えたら GLOW_TIERS も合わせること。
 */


const KV_LEGEND: LegendItem[] = [
  { color: '#29c3ff', label: '500 kV 以上' },
  { color: '#3fa9e8', label: '275–499 kV' },
  { color: '#4f93cc', label: '154–274 kV' },
  { color: '#4a7099', label: '66–153 kV' },
  { color: '#3f4c5e', label: '66 kV 未満 / 不明' },
]

/**
 * ★ 円の大きさは**対数**スケール。
 *
 * 面積比例（sqrt）だと最大級の火力が小規模水力の数十倍の直径になり、
 * 全国表示で画面を埋めてしまう（実測でそうなった）。
 * 対数なら 3 桁の差が 3 段階の差に圧縮され、大小関係は保ったまま共存できる。
 */
const LOG_RADIUS = (minLn: number, maxLn: number, rMin: number, rMax: number,
                    field: string): unknown[] => [
  'interpolate', ['linear'], ['ln', ['max', ['coalesce', ['get', field], 0], 1]],
  minLn, rMin, maxLn, rMax,
]

/** 電源種の配色 */
const FUEL_COLOR: unknown[] = [
  'match',
  ['coalesce', ['get', 'fuel'], ['get', 'kind'], ''],
  'nuclear', '#c56cf0',
  'coal', '#8d8d8d',
  'gas', '#4fa3ff',
  'oil', '#ff8c42',
  'hydro', '#3fc1c9',
  '水力', '#3fc1c9',
  '風力', '#7ee787',
  '地熱', '#ff6b6b',
  'バイオマス', '#b5a642',
  '#9aa5b1',
]

export const LAYERS: LayerDef[] = [
  // ── 送電網（静的骨格）──────────────────────────────────────
  {
    id: 'grid_lines',
    order: 2,
    group: '送電網',
    label: '送電線（全量）',
    file: 'grid_lines.geojson',
    kind: 'line',
    defaultOn: true,
    note: 'AJG (OpenStreetMap 由来) 14,048 本・85,335 km。⛔ 幾何は一切簡略化していない',
    paint: {
      'line-color': KV_COLOR,
      // ⚠️ 細すぎると暗い底図の上でほぼ見えない。実測で 0.3px は不可視だった
      'line-width': KV_LINE_WIDTH,
      'line-opacity': 0.92,
    },
    legend: KV_LEGEND,
    popupFields: [
      { key: 'name', label: '線路名' },
      { key: 'kv', label: '電圧 (kV)' },
    ],
  },
  {
    id: 'substations',
    order: 4,
    group: '送電網',
    label: '変電所',
    file: 'substations.geojson',
    kind: 'circle',
    defaultOn: true,
    note: 'AJG 8,994 か所。色は送電線と同じ電圧区分',
    legend: KV_LEGEND,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 1.2, 8, 2.6, 12, 5.5],
      'circle-color': KV_COLOR,
      'circle-opacity': 0.9,
      'circle-stroke-width': 0.5,
      'circle-stroke-color': '#0b0f14',
    },
    popupFields: [
      { key: 'name', label: '変電所名' },
      { key: 'kv', label: '電圧 (kV)' },
      { key: 'region', label: '地域' },
    ],
  },

  // ── 潮流 ────────────────────────────────────────────────
  {
    id: 'flow_lines',
    group: '潮流',
    label: '基幹送電線 潮流（実測あり）',
    file: 'flow_lines.geojson',
    kind: 'line',
    defaultOn: true,
    note: 'OCCTO 公表 464 本のうち 330 本の実走線を復元（810 区間）。⛔ 経路が別電圧の線に迷い込んだ区間と長い直線代用は**描画しない**（180 区間 468km を除外）。線の色・太さは電圧、流光の色が**負荷率**（潮流÷想定容量）。⚠️ 容量は非公開のため電圧階級からの概算。⚠️ 経路はグラフ上の求解であり公式の線路図ではない',
    // ★ 底色は**送電線と完全に同じ**（電圧の青・同じ幅）。
    //   潮流の強弱は上を走る流光の色（黄→橙→赤）だけで表す。
    //   ⚠️ 以前は本体を橙にしていたため、黄の流光と合わさって
    //      「赤黄が交互に流れる線」になり、電圧も潮流量も読めなかった。
    order: 3,
    paint: {
      'line-color': KV_COLOR,
      'line-width': KV_LINE_WIDTH,
      'line-opacity': 0.92,
    },
    legend: [
      { color: '#fff7b0', label: '負荷率 0%' },
      { color: '#ffe14d', label: '10%' },
      { color: '#ffc81a', label: '20%' },
      { color: '#ffa50f', label: '30%' },
      { color: '#ff8412', label: '40%' },
      { color: '#ff631c', label: '55%' },
      { color: '#ff4124', label: '70%' },
      { color: '#e00000', label: '100%（容量到達）' },
      { color: '#8f0000', label: '120%（容量超過）' },
      { color: '#9aa5b1', label: '線の色と太さは電圧（青系）のまま', dashed: true },
    ],
    popupFields: [
      { key: 'line', label: '送電線名' },
      { key: 'area', label: 'エリア' },
      { key: 'kv', label: 'この区間の電圧 (kV)' },
      { key: 'line_kv', label: '線路の公称電圧 (kV)' },
      { key: 'mw', label: '当日平均|潮流| (MW)' },
      { key: 'capacity_mw', label: '想定運用容量 (MW)' },
      { key: 'util', label: '負荷率（推定）' },
      { key: 'from', label: '始点' },
      { key: 'to', label: '終点' },
      { key: 'km', label: '経路長 (km)' },
      { key: 'shared_count', label: 'この区間を共有する線路数' },
      { key: 'shared_with', label: '共有している線路' },
      { key: 'seg_quality', label: '区間の信頼度' },
      { key: 'seg_km', label: 'この区間の長さ (km)' },
      { key: 'geometry_source', label: '幾何の出所' },
    ],
  },

  // ── 発電 ────────────────────────────────────────────────
  {
    id: 'plants',
    order: 6,
    group: '発電',
    label: '調整力機組（実測）',
    file: 'plants.geojson',
    kind: 'circle',
    defaultOn: true,
    note: 'OCCTO 発電情報公表システムの 30 分実測。火力・原子力・水力（揚水中心）183 か所',
    paint: {
      // 対数スケール：ln(1e4)≈9.2（1万kWh）→ ln(9e7)≈18.3（9千万kWh）
      'circle-radius': [
        'interpolate', ['linear'], ['zoom'],
        4, LOG_RADIUS(9.2, 18.3, 2.0, 7.5, 'kwh'),
        11, LOG_RADIUS(9.2, 18.3, 4.0, 17, 'kwh'),
      ],
      'circle-color': FUEL_COLOR,
      // 潮流の光る下敷きの上でも埋もれないように、実測は不透明＋太い白フチ
      'circle-opacity': 0.95,
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 4, 1.4, 10, 2.2],
      'circle-stroke-color': '#f0f4fa',   // 実測は白フチ
    },
    legend: [
      { color: '#c56cf0', label: '原子力' },
      { color: '#8d8d8d', label: '火力（石炭）' },
      { color: '#4fa3ff', label: '火力（ガス）' },
      { color: '#ff8c42', label: '火力（石油）' },
      { color: '#3fc1c9', label: '水力・揚水' },
    ],
    popupFields: [
      { key: 'name', label: '発電所' },
      { key: 'operator', label: '事業者' },
      { key: 'fuel', label: '電源種' },
      { key: 'area', label: 'エリア' },
      { key: 'kwh', label: '当日発電量 (kWh)' },
      { key: 'data_quality', label: 'データ品質' },
      { key: 'coord_source', label: '座標の出所' },
      { key: 'match_method', label: '照合方法' },
    ],
  },
  {
    id: 'sites',
    order: 5,
    group: '発電',
    label: '非調整電源（推算）',
    file: 'sites.geojson',
    kind: 'circle',
    defaultOn: true,
    note: '⚠️ 推算値。エリア実績を設備容量比で按分したもので、個別実測ではない。水力・風力・地熱・バイオマス 624 か所',
    paint: {
      // 対数スケール：ln(1)=0（1MW）→ ln(350)≈5.9（最大級の一般水力）
      'circle-radius': [
        'interpolate', ['linear'], ['zoom'],
        4, LOG_RADIUS(0, 5.9, 1.5, 5.5, 'mw'),
        11, LOG_RADIUS(0, 5.9, 3.0, 13, 'mw'),
      ],
      'circle-color': FUEL_COLOR,
      // ⚠️ 推算は中空 + 破線的な弱い塗り。実測（白フチ・塗りあり）と見分けがつくこと
      'circle-opacity': 0.22,
      'circle-stroke-width': 1.6,
      'circle-stroke-color': FUEL_COLOR,
    },
    legend: [
      { color: '#3fc1c9', label: '水力（一般）' },
      { color: '#7ee787', label: '風力' },
      { color: '#ff6b6b', label: '地熱' },
      { color: '#b5a642', label: 'バイオマス' },
    ],
    popupFields: [
      { key: 'name', label: '発電所' },
      { key: 'operator', label: '事業者' },
      { key: 'kind', label: '電源種' },
      { key: 'mw', label: '認可出力 (MW)' },
      { key: 'mwh', label: '当日推算発電量 (MWh)' },
      { key: 'method', label: '発電方式' },
      { key: 'data_quality', label: 'データ品質' },
    ],
  },
]

/** 侧边栏分组顺序 */
export const GROUP_ORDER = ['潮流', '発電', '送電網']
