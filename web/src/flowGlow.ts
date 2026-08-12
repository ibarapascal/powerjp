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
import { slotNumAt } from './slots'

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
 * `line-dasharray` は線幅の倍数で、しかも配列そのものは interpolate できない。
 * そこで zoom ごとの配列を `step` に詰める。
 *
 * Mapbox は zoom 式を整数 zoom で評価するため、4 未満は w4、5〜11 は
 * その整数 zoom での補間幅、11 以上は w11 を使う。整数の間では線幅だけが
 * 滑らかに変わるので完全一致ではないが、現在の「常に w11 基準」よりずっと
 * 小さい誤差で、zoom 中に setPaintProperty を追加で呼ばずに済む。
 */
function makeZoomDashSeq(w4: number, w11: number): unknown[][] {
  const seqByZoom = new Map<number, number[][]>()
  for (let z = 4; z <= 11; z += 1) {
    const width = w4 + (w11 - w4) * ((z - 4) / 7)
    seqByZoom.set(z, makeDashSeq(width))
  }

  return Array.from({ length: FRAMES }, (_, k) => {
    // zoom < 5（zoom 3/4 を含む）は w4 の系列。
    const expr: unknown[] = ['step', ['zoom'], seqByZoom.get(4)![k]]
    for (let z = 5; z <= 11; z += 1) {
      expr.push(z, seqByZoom.get(z)![k])
    }
    return expr
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
  const seq = makeZoomDashSeq(t.w4, t.w11)
  return [
    { ...t, id: `flow_glow_${t.key}_fwd`, dir: 1 as const, seq },
    { ...t, id: `flow_glow_${t.key}_rev`, dir: -1 as const, seq },
  ]
})

/**
 * コマ i の潮流値（符号つき）。**欠測は 0**（向きの判定で自然に除かれる）。
 * ⛔ `['at', i, ['get','slots_mw']]` と書き直さないこと —— 理由は `slots.ts`。
 */
export const slotAt = (i: number): unknown[] => slotNumAt(i)

/**
 * 帯 t・コマ i のフィルタ。電圧帯で絞り、さらに**符号で向きを振り分ける**。
 * ⚠️ 欠測は `slotAt` が 0 にするので、`> 0` にも `< 0` にも入らず自然に落ちる。
 *    以前あった `['!=', …, null]` は不要になった（`at` の結果が value 型なので
 *    null 比較自体は書けるが、二重に判定する意味がない）。
 */
export function glowFilter(t: (typeof GLOW_TIERS)[number], i: number): unknown[] {
  const kv = ['coalesce', ['get', 'kv'], 0]
  const band: unknown[] = t.max === null
    ? ['>=', kv, t.min]
    : ['all', ['>=', kv, t.min], ['<', kv, t.max]]
  return ['all',
    band,
    t.dir > 0 ? ['>', slotAt(i), 0] : ['<', slotAt(i), 0],
  ]
}

/**
 * ★ 負荷率の色標。**これがただ一つの定義**。
 *
 * 地図の流光（Mapbox の `interpolate` 式）と、サイドバーの凡例（CSS の
 * `linear-gradient`）が**同じ配列から**生成される。数値を 2 か所に書くと、
 * 片方を直したときに凡例だけ古い色を出し続ける。
 *
 * ⚠️ 色標は**実際の分布**に合わせて低い側を密にしてある。
 *    実測（当日平均）: p25 9% / p50 19% / p75 40% / p90 56% / p95 68%
 *    等間隔に置くと過半数の区間が同じ黄色になり、時間帯ごとの差が読めない。
 *    ⛔ 「等間隔の方が素直だから」で直さないこと。
 */
export const UTIL_RAMP: [number, string][] = [
  [0.00, '#fff7b0'],   //   0%  ほぼ無負荷（淡黄）
  [0.10, '#ffe14d'],   //  10%  p25
  [0.20, '#ffc81a'],   //  20%  p50
  [0.30, '#ffa50f'],   //  30%
  [0.40, '#ff8412'],   //  40%  p75
  [0.55, '#ff631c'],   //  55%  p90
  [0.70, '#ff4124'],   //  70%  p95
  [0.85, '#ff1f1f'],   //  85%
  [1.00, '#e00000'],   // 100%  赤（容量到達）
  [1.20, '#8f0000'],   // 120%  暗赤（容量超過）
]

/** 上の色標を CSS の `linear-gradient(90deg, …)` に直す。凡例はこれを使う */
export const utilGradientCss = (): string => {
  const max = UTIL_RAMP[UTIL_RAMP.length - 1][0]
  const stops = UTIL_RAMP.map(([v, c]) => `${c} ${(v / max * 100).toFixed(1)}%`)
  return `linear-gradient(90deg, ${stops.join(', ')})`
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
// 全国表示では短い線分がほぼサブピクセルになり、流光を動かすほど
// ラスタライズの有無が明滅する。潮流線と静止した光段は残し、局所表示
// （zoom 5 以上）でだけ方向のアニメーションを再開する。
const FLOW_ANIMATION_MIN_ZOOM = 5

type WithRaf = mapboxgl.Map & {
  __flowRaf?: number | null
  /** この時刻までは位相を進めない（スライダー操作中） */
  __flowHoldUntil?: number
  /** 停止を始めた時刻。再開時に skew へ足す */
  __flowHoldFrom?: number | null
  /** 停止していた合計時間。位相はこれを差し引いた時刻から出す */
  __flowSkew?: number
  /** style 更新失敗時は一度だけ警告し、エラー自体で主スレッドを埋めない */
  __flowFrameWarned?: boolean
}

/**
 * 流光を一時停止する。**スライダーを操作している間は光を流さない。**
 *
 * コマを動かすと線の色（負荷率）と向き（符号）が同時に変わる。そこに
 * 光の流れが重なると、色の変化なのか光が動いたのか区別できず、
 * 「何が変わったか」が読めない。操作中は色だけを変える。
 *
 * 呼ぶたびに期限が延びるので、ドラッグ中は止まったまま、
 * 手が止まって `ms` 経ったら自然に再開する（ボタンを離したかは見ない
 * —— 押したまま止めている間も「読んでいる」ので、そこで流れ出してよい）。
 */
export function holdFlowAnimation(m: mapboxgl.Map, ms = 300) {
  (m as WithRaf).__flowHoldUntil = performance.now() + ms
}

export function startFlowAnimation(m: mapboxgl.Map) {
  const mm = m as WithRaf
  if (mm.__flowRaf != null) return         // 同じ map に二重に張らない
  let lastIdx = -1
  const tick = () => {
    if (!mm.getLayer || !mm.getLayer(GLOW_TIERS[0].id)) {
      mm.__flowRaf = null
      return
    }
    // 遠景では光を動かさない。`line-dasharray` 自体は zoom 式なので、
    // 地図を縮小しても静止した光段の長さはその zoom に合わせて変わる。
    // RAF は残す（拡大した次のフレームで自然に再開するため）。
    if (mm.getZoom() < FLOW_ANIMATION_MIN_ZOOM) {
      lastIdx = -1
      mm.__flowRaf = requestAnimationFrame(tick)
      return
    }
    // ── 停止中は位相を進めない ──
    const now = performance.now()
    if (now < (mm.__flowHoldUntil ?? 0)) {
      if (mm.__flowHoldFrom == null) mm.__flowHoldFrom = now
      mm.__flowRaf = requestAnimationFrame(tick)
      return
    }
    // ⚠️ 再開時、止まっていた時間を差し引く。差し引かないと位相が
    //    実時間ぶん飛んで、光が瞬間移動したように見える。
    if (mm.__flowHoldFrom != null) {
      mm.__flowSkew = (mm.__flowSkew ?? 0) + (now - mm.__flowHoldFrom)
      mm.__flowHoldFrom = null
    }
    // ⚠️ 毎フレーム setPaintProperty すると（60fps）dash テクスチャの
    //    再生成が間に合わず明滅する。フレーム番号が変わった時だけ書く。
    const t = now - (mm.__flowSkew ?? 0)
    const idx = Math.floor(((t % CYCLE_MS) / CYCLE_MS) * FRAMES)
    if (idx !== lastIdx) {
      lastIdx = idx
      try {
        for (const t of GLOW_TIERS) {
          // ⚠️ 帯ごとに専用の系列を使う（dash の単位が線幅依存のため）
          // ★ 逆向きの帯は**位相を逆に進める**。同じ系列を後ろから読むだけで
          //   光が反対へ流れる。系列を別に作る必要はない。
          const k = t.dir > 0 ? idx % FRAMES : (FRAMES - (idx % FRAMES)) % FRAMES
          if (mm.getLayer(t.id)) {
            // @types/mapbox-gl は array<number> しか表現できないが、Mapbox
            // 実装はここで `step(zoom, array, ...)` も受け付ける。
            mm.setPaintProperty(t.id, 'line-dasharray', t.seq[k] as never)
          }
        }
        mm.__flowFrameWarned = false
      } catch (err) {
        // Windows/ANGLE では、スライダーの style 更新と同じフレームに
        // 重なると Mapbox が一時的に style setter を拒否することがある。
        // ここで RAF 自体を捨てると、以後ずっと光が止まるので、このフレーム
        // だけ捨てて次のフレームで再試行する。
        lastIdx = -1
        if (!mm.__flowFrameWarned) {
          console.warn('[powerjp] 流光フレームをスキップ:', err)
          mm.__flowFrameWarned = true
        }
      }
    }
    mm.__flowRaf = requestAnimationFrame(tick)
  }
  mm.__flowRaf = requestAnimationFrame(tick)
}

export function stopFlowAnimation(m: mapboxgl.Map) {
  const mm = m as WithRaf
  if (mm.__flowRaf != null) cancelAnimationFrame(mm.__flowRaf)
  mm.__flowRaf = null
  mm.__flowFrameWarned = false
}
