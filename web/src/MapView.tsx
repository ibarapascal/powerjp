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
import { registerPieIcons } from './pieIcons'
import { GLOW_TIERS, startFlowAnimation, stopFlowAnimation } from './flowGlow'
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
      // 扇形アイコンはスタイル読み込みのたびに入れ直す必要がある
      try {
        registerPieIcons(m as never)
      } catch (err) {
        console.error('[powerjp] 扇形アイコンの登録に失敗:', err)
      }
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
        // ★ source は **file 単位**。同じ GeoJSON を複数レイヤーで
        //   filter 違いで使い分ける（送電線を 3 レイヤーに分けている）。
        //   layer ごとに source を作ると同じデータを何度も持つことになる。
        const srcId = def.file
        if (!m.getSource(srcId)) m.addSource(srcId, { type: 'geojson', data })
        if (!m.getLayer(def.id)) {
          m.addLayer({
            id: def.id,
            type: def.kind,
            source: srcId,
            ...(def.filter ? { filter: def.filter as never } : {}),
            ...(def.minzoom ? { minzoom: def.minzoom } : {}),
            ...(def.layout ? { layout: def.layout as never } : {}),
            paint: def.paint as never,
          } as never)
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
          // ⚠️ source は**ファイル名**で作られる（同じ GeoJSON を複数レイヤーで
          //    共有するため）。レイヤー id ではないので間違えると
          //    addLayer が例外を投げ、潮流ごと消える（実際に消えた）。
          source: 'flow_lines.geojson',
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
