/**
 * Mapbox GL JS の薄いラッパ。レイヤーは `layers.ts` の宣言から動的に追加する。
 *
 * ## ⚠️ PMTiles は使えない
 * `addProtocol` は MapLibre の API で、Mapbox GL JS には無い（LESSONS #2）。
 * タイル化する場合は `{z}/{x}/{y}.pbf` のディレクトリ形式にすること。
 *
 * ## データは遅延読込
 * grid_lines は 7.7 MB あるので、チェックを入れた時に初めて fetch する。
 * 一度読んだ source は消さずに visibility だけ切り替える（再取得を避ける）。
 */
import { useEffect, useRef } from 'react'
import mapboxgl from 'mapbox-gl'
import { LAYERS } from './layers'
import type { PickedFeature } from './types'

type Props = {
  token: string
  enabled: Set<string>
  onPick: (f: PickedFeature | null) => void
  onLoadingChange: (id: string, busy: boolean) => void
}

// 初期表示は首都圏。全国からだと線が細く潮流も判別できないので、
// 「まず何が見えるか」が伝わる縮尺で開く。全国は引けば見える。
const INITIAL_CENTER: [number, number] = [139.75, 35.75]   // 東京
const INITIAL_ZOOM = 8.2

export function MapView({ token, enabled, onPick, onLoadingChange }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const map = useRef<mapboxgl.Map | null>(null)
  const ready = useRef(false)
  /** 既に fetch 済みの layer id。再取得を避けるため消さない */
  const loaded = useRef<Set<string>>(new Set())
  const pending = useRef<Set<string>>(new Set())

  // ── 初期化（1 回だけ）──
  useEffect(() => {
    if (map.current || !ref.current) return
    mapboxgl.accessToken = token
    const m = new mapboxgl.Map({
      container: ref.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: INITIAL_CENTER,
      zoom: INITIAL_ZOOM,
      minZoom: 3,
      maxZoom: 15,
      attributionControl: false,
      // WebGL の描画バッファを保持する。無効（既定）だとスクリーンショット系の
      // ツールで canvas が真っ黒に写り、「地図が出ていない」と誤診する。
      // 目視・自動検証のコストの方が、わずかな描画オーバーヘッドより大きい。
      preserveDrawingBuffer: true,
    })
    m.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right')
    m.addControl(
      new mapboxgl.AttributionControl({
        customAttribution:
          'OCCTO · AJG/OpenStreetMap (ODbL) · GEM · 国土数値情報',
      }),
      'bottom-right',
    )
    // dev のみ：デバッグ用に map インスタンスを露出
    if (import.meta.env.DEV) (window as unknown as { __map?: mapboxgl.Map }).__map = m
    m.on('error', (e) => console.error('[powerjp] mapbox error:', e?.error ?? e))
    // ⚠️ `load` ではなく `style.load` を使う。
    //    `load` は**全タイルの読込完了**を待つイベントで、回線が遅い・
    //    タブが非アクティブ等で永久に発火しないことがある。実測でも
    //    `loaded()` が false のままレイヤーが 1 つも追加されなかった。
    //    addSource/addLayer に必要なのはスタイルの準備完了だけなので style.load で十分。
    m.on('style.load', () => {
      // dark-v11 は陸も海もほぼ真っ黒で、海岸線が判別できない。
      // 送電網の形だけが浮かぶのは綺麗だが地理的な参照点が無いので、
      // 陸をわずかに持ち上げて輪郭が分かるようにする。
      try {
        m.setPaintProperty('land', 'background-color', '#151b23')
        m.setPaintProperty('water', 'fill-color', '#080b0f')
      } catch (err) {
        console.warn('[powerjp] 底図の色調整をスキップ:', err)
      }
      ready.current = true
      syncLayers(m, enabled, loaded, pending, onLoadingChange, onPick)
    })
    map.current = m
    return () => {
      stopFlowAnimation(m)
      m.remove()
      map.current = null
      ready.current = false
      loaded.current.clear()
    }
    // token は実質固定。enabled は下の effect で追従する
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  // ── レイヤーの ON/OFF に追従 ──
  useEffect(() => {
    const m = map.current
    if (!m || !ready.current) return
    syncLayers(m, enabled, loaded, pending, onLoadingChange, onPick)
  }, [enabled, onLoadingChange, onPick])

  return <div ref={ref} className="map" />
}

async function syncLayers(
  m: mapboxgl.Map,
  enabled: Set<string>,
  loaded: React.MutableRefObject<Set<string>>,
  pending: React.MutableRefObject<Set<string>>,
  onLoadingChange: (id: string, busy: boolean) => void,
  onPick: (f: PickedFeature | null) => void,
) {
  for (const def of LAYERS) {
    const want = enabled.has(def.id)

    if (want && !loaded.current.has(def.id)) {
      if (pending.current.has(def.id)) continue
      pending.current.add(def.id)
      onLoadingChange(def.id, true)
      try {
        // 本番は CDN（VITE_DATA_BASE）、dev は vite プラグインが ../storage/publish を配信
        const base = (import.meta.env.VITE_DATA_BASE as string | undefined) ?? '/data'
        const res = await fetch(`${base.replace(/\/$/, '')}/${def.file}`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const data = await res.json()
        if (!m.getSource(def.id)) {
          m.addSource(def.id, { type: 'geojson', data })
          m.addLayer({
            id: def.id,
            type: def.kind,
            source: def.id,
            ...(def.minzoom ? { minzoom: def.minzoom } : {}),
            paint: def.paint as never,
          })
          m.on('click', def.id, (e) => {
            const f = e.features?.[0]
            // GeoJSONFeature の型に properties が無いので明示的に取り出す
            if (f) {
              const props = (f as unknown as { properties?: Record<string, unknown> })
                .properties
              onPick({ layer: def, props: props ?? {} })
            }
          })
          m.on('mouseenter', def.id, () => (m.getCanvas().style.cursor = 'pointer'))
          m.on('mouseleave', def.id, () => (m.getCanvas().style.cursor = ''))
        }
        loaded.current.add(def.id)
      } catch (err) {
        console.error(`[powerjp] レイヤー ${def.id} の読込に失敗:`, err)
      } finally {
        pending.current.delete(def.id)
        onLoadingChange(def.id, false)
      }
    }

    // 一度読んだものは消さずに表示切替だけ（再取得を避ける）
    if (m.getLayer(def.id)) {
      m.setLayoutProperty(def.id, 'visibility', want ? 'visible' : 'none')
      if (def.id === 'flow_lines') {
        for (const t of GLOW_TIERS) {
          if (m.getLayer(t.id)) {
            m.setLayoutProperty(t.id, 'visibility', want ? 'visible' : 'none')
          }
        }
      }
    }
  }

  // ★ 潮流の「流れる光」。dasharray を時間で動かして方向を表す。
  //   幾何は from → to（潮流の正方向）なので、dash が進む向き＝正方向。
  //
  // ⚠️ **電圧帯ごとに別レイヤーへ分ける**。
  //    `line-dasharray` は線幅ごとに dash テクスチャを生成する実装のため、
  //    `line-width` を data-driven（['step', ['get','kv'], …]）にすると
  //    1 レイヤー内に複数の幅が混在し、毎フレーム複数のテクスチャを
  //    作り直すことになって**一部の線だけがランダムに明滅する**。
  //    帯ごとに幅を定数（zoom 補間のみ）にすれば、テクスチャは帯ごとに
  //    1 つに固定され、明滅しない。
  if (m.getLayer('flow_lines') && !m.getLayer(GLOW_TIERS[0].id)) {
    for (const t of GLOW_TIERS) {
      try {
        m.addLayer({
          id: t.id,
          type: 'line',
          source: 'flow_lines',
          filter: t.max === null
            ? ['>=', ['coalesce', ['get', 'kv'], 0], t.min]
            : ['all',
               ['>=', ['coalesce', ['get', 'kv'], 0], t.min],
               ['<', ['coalesce', ['get', 'kv'], 0], t.max]],
          paint: {
            // ★ 流光の色＝**負荷率**（実潮流 ÷ 想定運用容量）。
            //   絶対値（MW）だと電圧階級ごとに桁が違い、500kV だけ赤くなって
            //   「その線が混んでいるか」が読めない。比率なら階級をまたいで比較できる。
            //   ⚠️ 容量は公開されていないため概算（publish 層 LINE_CAPACITY_MW）。
            'line-color': [
              'interpolate', ['linear'], ['coalesce', ['get', 'util'], 0],
              // ⚠️ 色標は**実際の分布**に合わせて低い側を密にする。
              //    実測（当日平均）: p25 9% / p50 19% / p75 40% / p90 56% / p95 68%
              //    等間隔に置くと過半数の区間が同じ黄色になり、時間帯ごとの
              //    差が読めない。0〜40% に 5 点置いて 3/4 の区間を describe する。
              0.00, '#fff7b0',   //   0%  ほぼ無負荷（淡黄）
              0.10, '#ffe14d',   //  10%  p25
              0.20, '#ffc81a',   //  20%  p50
              0.30, '#ffa50f',   //  30%
              0.40, '#ff8412',   //  40%  p75
              0.55, '#ff631c',   //  55%  p90
              0.70, '#ff4124',   //  70%  p95
              0.85, '#ff1f1f',   //  85%
              1.00, '#e00000',   // 100%  赤（容量到達）
              1.20, '#8f0000',   // 120%  暗赤（容量超過）
            ],
            // 幅は定数（zoom 補間のみ）。送電線の同電圧帯と同じ値
            'line-width': ['interpolate', ['linear'], ['zoom'], 4, t.w4, 11, t.w11],
            'line-opacity': 1,
            'line-dasharray': t.seq[0],
          },
        })
      } catch (err) {
        console.error(`[powerjp] ${t.id} の追加に失敗:`, err)
      }
    }
    startFlowAnimation(m)
  }

  // 重ね順を layers.ts の order どおりに再構成する。
  // moveLayer(id) は「最前面へ」なので、order の昇順に呼べば宣言順が再現される。
  const ordered = [...LAYERS].sort(
    (a, b) => (a.order ?? LAYERS.indexOf(a)) - (b.order ?? LAYERS.indexOf(b)),
  )
  for (const def of ordered) {
    if (m.getLayer(def.id)) m.moveLayer(def.id)
    // 流光は潮流本体の直上に貼り付けて一緒に動かす
    if (def.id === 'flow_lines') {
      for (const t of GLOW_TIERS) if (m.getLayer(t.id)) m.moveLayer(t.id)
    }
  }
}


/**
 * 流れる光のアニメーション。
 *
 * Mapbox には線に沿ったアニメーション機能が無いので、`line-dasharray` の
 * オフセットを少しずつずらして「流れている」ように見せる定番の手法を使う。
 * 配列は「間隔・実線・間隔」の並びで、合計長を保ったまま位相だけずらす。
 */
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
export const GLOW_TIERS = [
  { id: 'flow_glow_500', min: 500, max: null as number | null, w4: 3.2, w11: 7.4 },
  { id: 'flow_glow_275', min: 275, max: 500 as number | null, w4: 2.2, w11: 5.2 },
  { id: 'flow_glow_154', min: 154, max: 275 as number | null, w4: 1.5, w11: 3.6 },
  { id: 'flow_glow_66', min: 66, max: 154 as number | null, w4: 1.0, w11: 2.4 },
  { id: 'flow_glow_lo', min: 0, max: 66 as number | null, w4: 0.6, w11: 1.6 },
].map((t) => ({ ...t, seq: makeDashSeq(t.w11) }))

// 周期 9px を何 ms で流すか。速度 = 9px / CYCLE_MS。
// 1400ms では 6.4px/s で遅すぎたので 700ms（12.9px/s）に。
// ⚠️ これ以上速くすると 1 フレーム 44ms を割り、dash テクスチャの
//    再生成が追いつかずカクつき始める。
const CYCLE_MS = 700

type WithRaf = mapboxgl.Map & { __flowRaf?: number | null }

function startFlowAnimation(m: mapboxgl.Map) {
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
          if (mm.getLayer(t.id)) mm.setPaintProperty(t.id, 'line-dasharray', t.seq[idx % FRAMES])
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
