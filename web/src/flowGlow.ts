/**
 * 潮流の「流れる光」まわり。
 *
 * ## ⚠️ なぜ MapView から切り出したか
 *
 * React Fast Refresh は、1 つのファイルが**コンポーネントとそれ以外を
 * 同時に export** していると熱更新を諦めて invalidate する：
 *
 *     hmr invalidate /src/MapView.tsx
 *       Could not Fast Refresh ("GLOW_TIERS" export is incompatible)
 *
 * こうなるとページを手動で再読込しない限り変更が反映されず、
 * 「直したのに画面が変わらない」という状態が延々続く（実際に起きた）。
 * **コンポーネントのファイルからは、コンポーネント以外を export しない。**
 */
import type mapboxgl from 'mapbox-gl'

/**
 * 流光の位相列を**線幅ごとに**生成する。
 *
 * ## ⚠️ `line-dasharray` の単位は「線幅の倍数」であってピクセルではない
 *
 * 同じ [.., 2, ..] でも、
 *   500kV（7.4px）→ 実長 14.8px   見える
 *   66kV 未満（1.6px）→ 実長 3.2px  短すぎる
 * となり、細い線では 1 フレームの前進量が 0.8px しかなく、
 * サブピクセルの丸めで**光が消えたり出たりする**。
 *
 * そこで帯ごとに `dash = 目標px / 線幅` を計算し、
 * **画面上の見た目の長さと流れる速さを揃える**。
 *
 * ## 位相の作り方（向きを揃えること）
 *
 * 実線ブロックは常に [φ, φ+dash] に置く：
 *   φ ≤ total-dash : [0, φ, dash, total-dash-φ]
 *   それ以降       : [φ+dash-total, total-dash, total-φ, 0]
 * 要素数は常に 4（奇数だと dash と gap の役割が入れ替わって図柄が飛ぶ）。
 */
// ★ 周期は**最短の線分より短く**すること。
//
// 潮流線は経路上の辺ごとに feature を分けている（幅を各区間の実電圧に
// 合わせるため）。dasharray は feature の先頭から始まるので、
// 周期が線分より長いと**その線分に光が 1 つも乗らない時間**ができる。
//
//   実測（zoom 8.8）の線分長 中央値:
//     500kV 81px / 275kV 23px / 154kV 11px / 66kV **5px**
//   旧設定は周期 40px だったので、66kV では 1 区間あたり光 0.1 個。
//   これが「細い線で光が出たり消えたりする」正体だった。
//
// 周期 9px なら 66kV の 5px 区間でも常に光の一部が乗る。
// 光を長く・間隔を短く。地色（電圧の青）が見えすぎるという指摘への対応。
// ⚠️ 周期（DASH+GAP）は最短区間 5px を超えないこと。超えると光が乗らない
//    時間ができて「細い線で光が出たり消えたり」が再発する。
const DASH_PX = 4         // 光そのものの長さ（画面ピクセル）
const GAP_PX = 5          // 光と光の間隔 —— 光が 44% を占める
const FRAMES = 16

function makeDashSeq(widthPx: number): number[][] {
  const dash = DASH_PX / widthPx
  const total = (DASH_PX + GAP_PX) / widthPx
  const r = (v: number) => Math.round(v * 1000) / 1000
  // ⚠️ **要素に 0 を入れない**。Mapbox は長さ 0 のセグメントを
  //    ラスタライズ時に読み飛ばすため、[a, b, c, 0] は実質 3 要素として
  //    扱われ、奇数個 → 2 周目で実線と空白の役割が入れ替わる。
  //    そのフレームだけ光が消える（細い線ほど顕著）。
  const EPS = 0.002
  const clamp = (v: number) => (v < EPS ? EPS : r(v))
  return Array.from({ length: FRAMES }, (_, k) => {
    const phi = (total * k) / FRAMES
    if (phi <= total - dash) {
      // 実線ブロックが途中で切れない位置
      return [EPS, clamp(phi), r(dash), clamp(total - dash - phi - EPS)]
    }
    // 末尾から先頭へ回り込む位置。頭と尻に分かれる
    const head = phi + dash - total
    return [clamp(head), r(total - dash), clamp(total - phi), EPS]
  })
}


/**
 * 流光アニメーション。
 *
 * ## ⚠️ カウンタ加算ではなく**時刻から位相を計算する**
 *
 * 以前は `setInterval` + `i++` で位相を進めていた。すると：
 *
 * - StrictMode の二重マウントや map の再生成でループが**複数**走る
 * - それぞれが独立したカウンタを持ち、同じレイヤーに別々の位相を書き込む
 * - 結果、線がランダムに飛んで「一部だけチカチカする」
 *
 * 時刻から位相を出せば、ループが何本あっても同じ値になるので競合しない。
 * ループの停止漏れがあっても症状が出ない（べき等）。
 */
/**
 * 流光の電圧帯。幅は `layers.ts` の KV_LINE_WIDTH と同じ数値を帯ごとに定数化したもの。
 * ⚠️ KV_LINE_WIDTH を変えたらここも合わせること（data-driven にはできない。上の注記参照）。
 */
const TIER_WIDTHS = [
  { key: '500', min: 500, max: null as number | null, w4: 3.2, w11: 7.4 },
  { key: '275', min: 275, max: 500 as number | null, w4: 2.2, w11: 5.2 },
  { key: '154', min: 154, max: 275 as number | null, w4: 1.5, w11: 3.6 },
  { key: '66', min: 66, max: 154 as number | null, w4: 1.0, w11: 2.4 },
  { key: 'lo', min: 0, max: 66 as number | null, w4: 0.6, w11: 1.6 },
]

/**
 * ★ 帯 × **向き** でレイヤーを分ける（5 × 2 = 10）。
 *
 * ## なぜ向きでもレイヤーを分けるのか
 *
 * `line-dasharray` は **paint プロパティ**であって data-driven にできない。
 * つまり 1 レイヤーの中で「この線は順方向、あの線は逆方向」を出し分けられない。
 * 流れる向きは dash の位相を進める向きで表現しているので、
 * **向きごとにレイヤーを分ける以外に方法がない**。
 *
 * ## 幾何を反転させる案を採らなかった理由
 *
 * 以前は publish 側で「当日平均が負なら幾何を反転」していた。1 本 1 向きに
 * 決め打ちできたから成立していたが、コマ単位にすると **464 本中 155〜184 本
 * （33〜40%）が窓の中で向きを変える**。コマを動かすたびに `setData` で
 * 幾何を差し替える案も検討したが、1.5MB の GeoJSON を再パースして
 * タイルを作り直すことになり、スライダーを引くと確実に固まる。
 * `setFilter` なら幾何の再パースは起きない。
 */
export const GLOW_TIERS = TIER_WIDTHS.flatMap((t) => {
  const seq = makeDashSeq(t.w11)
  return [
    { ...t, id: `flow_glow_${t.key}_fwd`, dir: 1 as const, seq },
    { ...t, id: `flow_glow_${t.key}_rev`, dir: -1 as const, seq },
  ]
})

/** コマ i の潮流値（符号つき）。⚠️ 値が無いコマは null */
export const slotAt = (i: number): unknown[] => ['at', i, ['get', 'slots_mw']]

/**
 * 帯 t・コマ i のフィルタ。電圧帯で絞り、さらに**符号で向きを振り分ける**。
 * ⚠️ null（欠測）は明示的に除く。`['>', null, 0]` は評価できない。
 */
export function glowFilter(t: (typeof GLOW_TIERS)[number], i: number): unknown[] {
  const kv = ['coalesce', ['get', 'kv'], 0]
  const band: unknown[] = t.max === null
    ? ['>=', kv, t.min]
    : ['all', ['>=', kv, t.min], ['<', kv, t.max]]
  return ['all',
    band,
    ['!=', slotAt(i), null],
    t.dir > 0 ? ['>', slotAt(i), 0] : ['<', slotAt(i), 0],
  ]
}

/**
 * コマ i の負荷率 = |潮流| ÷ 想定運用容量。
 * ⚠️ 容量 0 で割らないよう `max(cap, 1)` で守る。
 */
export const utilAt = (i: number): unknown[] => [
  '/', ['abs', slotAt(i)], ['max', ['coalesce', ['get', 'capacity_mw'], 0], 1],
]

// 周期 9px を何 ms で流すか。速度 = 9px / CYCLE_MS。
// 1400ms では 6.4px/s で遅すぎたので 700ms（12.9px/s）に。
// ⚠️ これ以上速くすると 1 フレーム 44ms を割り、dash テクスチャの
//    再生成が追いつかずカクつき始める。
const CYCLE_MS = 700

type WithRaf = mapboxgl.Map & { __flowRaf?: number | null }

export function startFlowAnimation(m: mapboxgl.Map) {
  const mm = m as WithRaf
  if (mm.__flowRaf) return                 // 同じ map に二重に張らない
  let lastIdx = -1
  const tick = () => {
    if (!mm.getLayer || !mm.getLayer(GLOW_TIERS[0].id)) {
      mm.__flowRaf = null
      return
    }
    // ⚠️ 毎フレーム setPaintProperty すると（60fps）dash テクスチャの
    //    再生成が間に合わず明滅する。フレーム番号が変わった時だけ書く。
    const idx = Math.floor(((performance.now() % CYCLE_MS) / CYCLE_MS) * FRAMES)
    if (idx !== lastIdx) {
      lastIdx = idx
      try {
        for (const t of GLOW_TIERS) {
          // ⚠️ 帯ごとに専用の系列を使う（dash の単位が線幅依存のため）
          // ★ 逆向きの帯は**位相を逆に進める**。同じ系列を後ろから読むだけで
          //   光が反対へ流れる。系列を別に作る必要はない。
          const k = t.dir > 0 ? idx % FRAMES : (FRAMES - (idx % FRAMES)) % FRAMES
          if (mm.getLayer(t.id)) mm.setPaintProperty(t.id, 'line-dasharray', t.seq[k])
        }
      } catch {
        mm.__flowRaf = null                // map が破棄済みなら静かに止める
        return
      }
    }
    mm.__flowRaf = requestAnimationFrame(tick)
  }
  mm.__flowRaf = requestAnimationFrame(tick)
}

export function stopFlowAnimation(m: mapboxgl.Map) {
  const mm = m as WithRaf
  if (mm.__flowRaf) cancelAnimationFrame(mm.__flowRaf)
  mm.__flowRaf = null
}
