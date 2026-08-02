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
  kind: 'line' | 'circle' | 'symbol'
  /** mapbox paint 表达式 */
  paint: Record<string, unknown>
  /** symbol レイヤー用。icon-image / icon-size など */
  layout?: Record<string, unknown>
  /**
   * mapbox の filter 式。**同じ `file` を複数レイヤーで分け合う**ときに使う。
   * source は `file` 単位で 1 つだけ作られる（MapView 参照）。
   */
  filter?: unknown[]
  defaultOn: boolean
  /** 侧边栏里的一句话说明 —— 尤其要写清是实测还是推算 */
  note?: string
  legend?: LegendItem[]
  /** 点击要素时，信息面板按这个顺序显示属性。
   *  `map` は生の値（`voltage` のようなコード）を日本語に置き換える */
  popupFields?: { key: string; label: string; map?: Record<string, string> }[]
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
 * ★ 円の大きさ＝設備容量。全ての発電設備で**同一のスケール**を使う。
 *
 * ## ⚠️ レイヤーごとに別のスケールを持たせない
 *
 * 以前は plants（10〜5,400MW）と sites（1〜365MW）で別々の定義域を張っていた。
 * 結果 **30MW と 3,000MW がほぼ同じ大きさ**になり、
 * 「大きさ＝容量」という約束が成立していなかった。
 * 1MW〜6,000MW を全設備共通の定義域とする。
 *
 * ## ⚠️ スケールは「対数」でも「面積比例」でもなく **0.4 乗**
 *
 * | 30MW vs 3,000MW | 1MW | 30MW | 3,000MW |
 * |---|---|---|---|
 * | 対数 ln | 3.0px | 12.0px | 24.2px → **1:2.0** 差が出ない |
 * | 面積比例 sqrt | 3.0px | 4.3px | 19.2px → 1:4.4 だが中間が潰れる |
 * | **0.4 乗（採用）** | 2.5px | **5.4px** | **26.1px** → **1:4.8** |
 *
 * 上端は 26px → 34px に拡大（2026-08-02「大的可以稍微更大一点」）。
 *
 * 面積比例は「大きいものが大きすぎる」、対数は「小さいものが大きすぎる」。
 * 0.4 乗は両者の間で、100〜1,000MW の中間帯も潰れない。
 */
export const CAP_MIN_MW = 1
export const CAP_MAX_MW = 6000
const P = 0.4
const capT = (field: string): unknown[] => [
  '^', ['max', ['coalesce', ['get', field], CAP_MIN_MW], CAP_MIN_MW], P,
]
const T_MIN = 1          // 1^0.4
const T_MAX = 32.3       // 6000^0.4

/**
 * 容量（MW）→ 半径（px）を **JS で**計算する。
 *
 * ⚠️ 凡例（Sidebar の同心円）はこれを使うこと。
 *    Mapbox の式を目で読んで別に数値を書くと、片方を直したときにずれる。
 *    地図と凡例は**同じ関数**から出す。
 */
export function capRadiusPx(mw: number, zoom: 4 | 11 = 11): number {
  const t = (Math.max(mw, CAP_MIN_MW) ** P - T_MIN) / (T_MAX - T_MIN)
  const [lo, hi] = zoom === 4 ? [1.2, 14.0] : [2.5, 34.0]
  return lo + (hi - lo) * Math.max(0, Math.min(1, t))
}

/**
 * 容量（MW）→ 輪郭の太さ（px）。
 *
 * ⚠️ 固定値にしてはいけない。半径 2.5px の円に 1.6px の輪郭を引くと
 *    円の 6 割が輪郭になり、小さい点ほど太く見える。
 *    半径と同じスケールに連動させ、輪郭が半径に占める割合を揃える。
 */
export const capStrokeWidth = (field: string): unknown[] => [
  'interpolate', ['linear'], ['zoom'],
  4, ['interpolate', ['linear'], capT(field), T_MIN, 0.4, T_MAX, 1.6],
  11, ['interpolate', ['linear'], capT(field), T_MIN, 0.6, T_MAX, 3.0],
]

/** 容量（MW）→ 半径（px）。circle レイヤー用 */
export const capRadius = (field: string): unknown[] => [
  'interpolate', ['linear'], ['zoom'],
  4, ['interpolate', ['linear'], capT(field), T_MIN, 1.2, T_MAX, 14.0],
  11, ['interpolate', ['linear'], capT(field), T_MIN, 2.5, T_MAX, 34.0],
]

/**
 * 同じスケールを symbol の icon-size に直したもの。
 *
 * ⚠️ 除数は 64 ではなく **56**。`pieIcons.ts` が描く円の半径は
 *    `ICON_PX/2 - 4 = 28`、つまり**直径 56px** で、画像の 64px 全部を
 *    使っていない。64 で割ると実際の円が 12.5% 小さくなり、
 *    同じ容量でも circle レイヤーより小さく見える。
 * ⚠️ zoom 補間は入れ子にできないので換算済みの数値を直接書く
 *    （2r/56 を計算したもの）。
 */
export const capIconSize = (field: string): unknown[] => [
  'interpolate', ['linear'], ['zoom'],
  4, ['interpolate', ['linear'], capT(field), T_MIN, 0.043, T_MAX, 0.500],
  11, ['interpolate', ['linear'], capT(field), T_MIN, 0.089, T_MAX, 1.214],
]

/**
 * 電源種の配色。
 *
 * ## 参考にした配色（Our World in Data / grapher の EnergyColors）
 *
 *     Coal #883039 / Oil #c15065 / Gas #6d3e91 / Nuclear #00847e
 *     Hydro #286bbb / Wind #00295b / Solar #e56e5a / Biomass #9a5129
 *
 * ⚠️ そのままは使えない。OWID は **Gas が紫・Nuclear が青緑**で、
 * 「火力 3 種は同一色系」という方針に合わないうえ、
 * その青緑は本プロジェクトの水力とぶつかる。
 * 借りたのは考え方の方 ——「石炭は暗い赤系」「原子力は火力と**色相ごと**離す」。
 *
 * ## 火力は暖色で**明度**の段階を作る（炭素強度の順）
 *
 * | | 色 | 明度 | 背景比 |
 * |---|---|---|---|
 * | 火力（ガス） | `#ffd54a` | 0.69 | 13.6 |
 * | 火力（石油） | `#f57c1f` | 0.34 | 7.1 |
 * | 火力（石炭） | `#d13b30` | 0.17 | 4.0 |
 *
 * 相互コントラスト 1.78〜3.40。暖色 3 つが限界で、**4 つ目は入らない**
 * （試しに その他火力 を茶にしたら石炭との比が 1.04 ＝ 区別不能だった）。
 *
 * ## 「その他火力」は中性色にする
 *
 * OCCTO の「その他」「空欄」は製鉄所などの自家発（高炉ガス等の副生ガス）が
 * 大半だが**内訳は公表されない**。暖色に押し込むより、成分不明であることを
 * 中性色で示すほうが正直。`#7e8a97` は全ての暖色と色相が反対で見分けやすい。
 * ⚠️ 明度は原子力と近い（0.25 対 0.27）が、色相が 180° 違うので実用上は分かる。
 *
 * ## その他
 *
 * - 原子力は紫。火力の暖色とも水力の青とも色相が離れている
 * - 地熱・バイオマスは火力の暖色とぶつからない色へ（旧 `#ff6b6b` は石炭の赤と、
 *   旧 `#b5a642` はガスの黄と紛らわしかった）
 *
 * ⚠️ `pieIcons.ts` の FUEL_COLORS と**必ず同じ値**にすること。
 *    扇形アイコンは canvas で焼くため実際の色値が要り、式を共有できない。
 */
const FUEL_COLOR: unknown[] = [
  'match',
  ['coalesce', ['get', 'fuel'], ['get', 'kind'], ''],
  'nuclear', '#b06ff5',
  'gas', '#ffd54a',            // 火力：黄（炭素強度 低）
  'oil', '#f57c1f',            // 火力：橙
  'coal', '#d13b30',           // 火力：赤（炭素強度 高）
  'other_thermal', '#7e8a97',  // その他火力：中性（成分不明）
  'hydro', '#35c4d4',
  '水力', '#35c4d4',
  '風力', '#7dd3fc',
  '地熱', '#f472b6',
  'バイオマス', '#84cc16',
  '#9aa5b1',
]

export const LAYERS: LayerDef[] = [
  // ── 送電網（静的骨格）──────────────────────────────────────
  {
    id: 'grid_lines',
    order: 2,
    group: '送電網',
    label: '送電線（系統に接続）',
    file: 'grid_lines.geojson',
    kind: 'line',
    defaultOn: true,
    // ★ 拓撲図に含まれる線。潮流の経路探索に使えるのはこれだけ
    filter: ['all',
      ['==', ['get', 'in_topology'], true],
      ['!=', ['get', 'underground'], true]],
    note: 'AJG (OpenStreetMap 由来)。拓撲図に含まれ、潮流の経路探索に使える線。⛔ 幾何は一切簡略化していない',
    paint: {
      'line-color': KV_COLOR,
      'line-width': KV_LINE_WIDTH,
      'line-opacity': 0.92,
    },
    legend: KV_LEGEND,
    popupFields: [
      { key: 'name', label: '線路名' },
      { key: 'kv', label: '電圧 (kV)' },
      { key: 'power', label: '種別' },
      { key: 'in_topology', label: '拓撲図に含まれる' },
    ],
  },
  {
    id: 'grid_lines_extra',
    order: 1,
    group: '送電網',
    label: '送電線（接続情報なし）',
    file: 'grid_lines.geojson',
    kind: 'line',
    defaultOn: true,
    // ⚠️ 原データにはあるが AJG の拓撲図に入らなかった線（26,901 本／全体の 66%）。
    //    地図には描けるが**潮流の経路探索には使えない**。
    //    「描けるか」と「拓撲が計算できるか」は別問題なので、
    //    隠さずに一段暗くして区別する（REQUIREMENTS P2「不要缺东西」）。
    filter: ['all',
      ['!=', ['get', 'in_topology'], true],
      ['!=', ['get', 'underground'], true]],
    note: '原データにはあるが拓撲図に入らなかった線。地図には出るが潮流計算には使われない',
    paint: {
      'line-color': KV_COLOR,
      'line-width': KV_LINE_WIDTH,
      // 一段だけ暗く。消さずに主従を付ける
      'line-opacity': 0.45,
    },
    legend: [{ color: '#4f93cc', label: '接続情報が無い線（計算には未使用）' }],
    popupFields: [
      { key: 'name', label: '線路名' },
      { key: 'kv', label: '電圧 (kV)' },
      { key: 'power', label: '種別' },
      { key: 'in_topology', label: '拓撲図に含まれる' },
    ],
  },
  {
    id: 'grid_underground',
    order: 3,
    group: '送電網',
    label: '地下ケーブル',
    file: 'grid_lines.geojson',
    kind: 'line',
    defaultOn: true,
    filter: ['==', ['get', 'underground'], true],
    note: '地下・水底のケーブル 1,271 本。⚠️ 都市部の地下線は OSM 自体にほとんど無く、これは実在するもののごく一部',
    paint: {
      'line-color': KV_COLOR,
      'line-width': KV_LINE_WIDTH,
      // 拓撲に入っているものだけ濃く（opacity は属性で変えられる）
      'line-opacity': ['case', ['==', ['get', 'in_topology'], true], 0.92, 0.45],
      // ⚠️ 破線は**静的**。潮流の流光と違ってアニメーションしないので、
      //    data-driven な線幅と併用してもテクスチャの作り直しは起きない。
      'line-dasharray': [2, 1.5],
    },
    legend: [{ color: '#6ba3d6', label: '地下・水底ケーブル（破線）', dashed: true }],
    popupFields: [
      { key: 'name', label: '線路名' },
      { key: 'kv', label: '電圧 (kV)' },
      { key: 'power', label: '種別' },
      { key: 'in_topology', label: '拓撲図に含まれる' },
    ],
  },
  {
    id: 'substations',
    order: 5,
    group: '送電網',
    label: '変電所・開閉所ほか',
    file: 'substations.geojson',
    kind: 'circle',
    defaultOn: true,
    // ⚠️ 「変電所 8,994 か所」は不正確だった。この 8,994 点には配電用・鉄道用・
    //    需要家の自家用、さらに「所」ですらない架空/地中の切替所まで混じる。
    //    色は電圧のまま（種別で分けない）が、内訳は必ず示す。
    note: 'AJG 8,994 か所。内訳＝配電用 4,235／送電用 2,690／鉄道用 789／'
      + '需要家 352／架空・地中の切替所 169／開閉所 137／発電所構内 45／'
      + '変換所 28／調相設備 15／種別不明 534。'
      + 'うち 619 か所は OCCTO 基幹送電線の端点に名前が出る＝基幹系統。'
      + '色は種別ではなく電圧区分',
    legend: KV_LEGEND,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 1.2, 8, 2.6, 12, 5.5],
      'circle-color': KV_COLOR,
      // ⚠️ 輪郭を付けない。変電所は 8,994 か所あり、半径 1.2〜5.5px と小さい。
      //    そこに輪郭を足すと点が輪郭で埋まり、電圧の色が読めなくなる
      //    （2026-08-02 に 0.5px の暗色輪郭を廃止）。
      //    密集して潰れるのは想定内 —— 隠すのではなく不透明度で調整する（P2）。
      'circle-opacity': 0.85,
    },
    popupFields: [
      { key: 'name', label: '名称' },
      { key: 'stype_ja', label: '種別' },
      // 種別が実データ由来か推定かを隠さない。voltage は電圧からの推定
      {
        key: 'stype_source', label: '種別の根拠',
        map: {
          osm: 'OSM の substation タグ',
          name: '名称（「開閉所」など）',
          operator: '事業者名',
          voltage: '⚠️ 電圧からの推定',
          none: '判定できず',
        },
      },
      { key: 'kv', label: '電圧 (kV)' },
      { key: 'trunk', label: 'OCCTO 基幹系統' },
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
    note: 'OCCTO 公表 464 本のうち 330 本の実走線を復元（684 区間）。⛔ 経路が別電圧の線に迷い込んだ区間と長い直線代用は**描画しない**。線の色・太さは電圧、流光の色が**負荷率**（潮流÷想定容量）、流光の向きが**その時刻の実際の流れる向き**。直近 72 時間を 30 分刻みで遡れる（既定は最新）。⚠️ 実測 464 本中 155 本（33%）は日によって向きが変わるため、向きは時刻ごとに判定している。⚠️ 容量は非公開のため電圧階級からの概算。⚠️ 経路はグラフ上の求解であり公式の線路図ではない',
    // ★ 底色は**送電線と完全に同じ**（電圧の青・同じ幅）。
    //   潮流の強弱は上を走る流光の色（黄→橙→赤）だけで表す。
    //   ⚠️ 以前は本体を橙にしていたため、黄の流光と合わさって
    //      「赤黄が交互に流れる線」になり、電圧も潮流量も読めなかった。
    order: 4,   // ⚠️ grid_underground(3) と重ならないこと。同値だと重ね順が不定になる
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
      { key: 'capacity_mw', label: '想定運用容量 (MW)' },
      // ⚠️ from → to は OCCTO の**基準方向**。実際の向きはコマの符号で決まる
      //    （上の「このコマの潮流」に実際の向きが出る）
      { key: 'from', label: '基準方向の始点' },
      { key: 'to', label: '基準方向の終点' },
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
    order: 7,
    group: '発電',
    label: '調整力機組（実測）',
    file: 'plants.geojson',
    kind: 'symbol',
    defaultOn: true,
    note: 'OCCTO 発電情報公表システムの 30 分実測。円の大きさ＝設備容量、中の扇形＝**その時刻の出力÷容量**。火力・原子力・水力（揚水中心）183 か所',
    // ★ 円＝容量、扇形＝実発電。`circle` では扇形が描けないので symbol を使う。
    //   アイコンは pieIcons.ts が「電源種 × 利用率 21 段」で焼いて登録する。
    layout: {
      // データ側の fuel / cf からアイコン ID を組み立てる。
      // ⚠️ pieIcons.ts の iconId() と同じ規則にすること。
      'icon-image': [
        'concat',
        'pie-',
        ['match', ['coalesce', ['get', 'fuel'], ''],
          'nuclear', 'nuclear', 'coal', 'coal', 'gas', 'gas',
          'oil', 'oil', 'hydro', 'hydro', 'geothermal', 'geothermal',
          'other_thermal', 'other_thermal',
          'other'],
        '-',
        // ⚠️ 扇形は**その時刻の出力 ÷ 容量**（`load`）。
        //    当日平均（`cf`）ではない —— 昼だけ全開の機組が 40% に見えたり、
        //    既に停止した機組が動いているように見えてしまう。
        //    実測：原町は実時刻 94% に対し当日平均 48%、船町は 0% に対し 39%。
        ['to-string', ['round', ['*', ['coalesce', ['get', 'load'], 0], 20]]],
      ],
      // ⚠️ icon-size は**元画像 64px に対する倍率**。直径 D px にしたいなら D/64。
      //   大きさは設備容量の**対数**（面積比例だと最大級が画面を埋める）。
      'icon-size': capIconSize('capacity_mw'),
      // ⛔ 重なっても隠さない（REQUIREMENTS P2「太密没事，不要缺东西」）
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
    },
    paint: {},
    legend: [
      { color: '#b06ff5', label: '原子力' },
      { color: '#ffd54a', label: '火力（ガス）' },
      { color: '#f57c1f', label: '火力（石油）' },
      { color: '#d13b30', label: '火力（石炭）' },
      { color: '#7e8a97', label: 'その他火力（副生ガス等・内訳不明）' },
      { color: '#35c4d4', label: '水力・揚水' },
      { color: '#e9eef5', label: '円＝設備容量（対数）／扇形＝その時刻の出力÷容量', dashed: true },
    ],
    popupFields: [
      { key: 'name', label: '発電所' },
      { key: 'operator', label: '事業者' },
      { key: 'fuel', label: '電源種' },
      { key: 'area', label: 'エリア' },
      { key: 'capacity_mw', label: '設備容量 (MW)' },
      { key: 'mw_now', label: 'この時刻の出力 (MW)' },
      { key: 'load', label: 'この時刻の負荷率' },
      { key: 'cf', label: '（参考）当日平均の利用率' },
      { key: 'kwh', label: '当日発電量 (kWh)' },
      { key: 'capacity_source', label: '容量の出所' },
      { key: 'data_quality', label: 'データ品質' },
      { key: 'coord_source', label: '座標の出所' },
      { key: 'match_method', label: '照合方法' },
    ],
  },
  {
    id: 'sites',
    order: 6,
    group: '発電',
    label: '非調整電源（推算）',
    file: 'sites.geojson',
    kind: 'circle',
    defaultOn: true,
    note: '⚠️ 推算値。エリア実績を設備容量比で按分したもので、個別実測ではない。水力・風力・地熱・バイオマス 624 か所',
    paint: {
      // ★ plants と**同一スケール**（1MW〜6,000MW）。別々にすると
      //   30MW と 3,000MW がほぼ同じ大きさになる
      'circle-radius': capRadius('mw'),
      'circle-color': FUEL_COLOR,
      // ⚠️ 推算は中空 + 破線的な弱い塗り。実測（白フチ・塗りあり）と見分けがつくこと
      'circle-opacity': 0.22,
      // ⚠️ 固定幅にすると小さい円が輪郭だらけになる（半径 2.5px に 1.6px）
      'circle-stroke-width': capStrokeWidth('mw'),
      'circle-stroke-color': FUEL_COLOR,
    },
    legend: [
      { color: '#35c4d4', label: '水力（一般）' },
      { color: '#7dd3fc', label: '風力' },
      { color: '#f472b6', label: '地熱' },
      { color: '#84cc16', label: 'バイオマス' },
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
