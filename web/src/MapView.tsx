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
import { registerPieIcons, pieIconExpr, plantsFilter } from './pieIcons'
import {
  GLOW_TIERS, glowFilter, utilAt, UTIL_RAMP,
  startFlowAnimation, stopFlowAnimation,
} from './flowGlow'
import type { PickedFeature } from './types'

type Props = {
  token: string
  enabled: Set<string>
  onPick: (f: PickedFeature | null) => void
  onLoadingChange: (id: string, busy: boolean) => void
  /** 実データから数えた件数（レイヤー id → 件数）を返す */
  onCounts: (c: Record<string, number>) => void
  /** 表示するコマ（`flow_stamps.json` の添字）。潮流の向きも色もこれで決まる */
  slotIdx: number
}

// 初期表示は首都圏。全国からだと線が細く潮流も判別できないので、
// 「まず何が見えるか」が伝わる縮尺で開く。全国は引けば見える。
const INITIAL_CENTER: [number, number] = [139.75, 35.75]   // 東京
const INITIAL_ZOOM = 8.2

export function MapView({
  token, enabled, onPick, onLoadingChange, onCounts, slotIdx,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const map = useRef<mapboxgl.Map | null>(null)
  const ready = useRef(false)
  /** syncLayers は非同期なので、レイヤー生成時に最新のコマを読めるよう ref で持つ */
  const slot = useRef(slotIdx)
  slot.current = slotIdx
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
    // ⛔ NavigationControl は置かない（ユーザー要求 2026-08-07）。
    //    ズームはホイールとピンチで足りる。地図の上に物を足さない。
    // ⛔ customAttribution も足さない —— 右下は Mapbox 既定の表記だけにする。
    //    ⚠️ ただし AJG/OSM は ODbL、OCCTO は「出典明記」が条件なので、
    //    出典表記そのものは消せない。サイドバー最下部の 1 行がその役目を担う。
    m.addControl(new mapboxgl.AttributionControl({ compact: true }), 'bottom-right')
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
      syncLayers(m, enabled, loaded, pending, onLoadingChange, onPick, onCounts, slot.current)
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
    syncLayers(m, enabled, loaded, pending, onLoadingChange, onPick, onCounts, slotIdx)
  }, [enabled, onLoadingChange, onPick, onCounts, slotIdx])

  // ── コマの移動に追従 ──
  //
  // ⚠️ ここで `setData` を呼んではいけない。1.5MB の GeoJSON を再パースして
  //    タイルを作り直すので、スライダーを引くと固まる。
  //    `setFilter` / `setPaintProperty` は幾何に触らないので軽い。
  useEffect(() => {
    const m = map.current
    if (!m || !ready.current) return
    if (slotIdx < 0) return
    for (const t of GLOW_TIERS) {
      if (!m.getLayer(t.id)) continue
      try {
        m.setFilter(t.id, glowFilter(t, slotIdx) as never)
        m.setPaintProperty(t.id, 'line-color', utilColor(slotIdx) as never)
      } catch (err) {
        console.error(`[powerjp] ${t.id} のコマ更新に失敗:`, err)
      }
    }
    // 機組の扇形も同じコマへ。潮流と別の時刻を映すと嘘になる
    if (m.getLayer('plants')) {
      try {
        m.setLayoutProperty('plants', 'icon-image', pieIconExpr(slotIdx) as never)
        // ★ 欠測コマの機組は描かない。0 で描くと「停止中」と区別できない
        m.setFilter('plants', plantsFilter(slotIdx) as never)
      } catch (err) {
        console.error('[powerjp] 扇形のコマ更新に失敗:', err)
      }
    }
  }, [slotIdx])

  return <div ref={ref} className="map" />
}

/**
 * 流光の色＝**負荷率**（実潮流 ÷ 想定運用容量）。
 *
 * 絶対値（MW）だと電圧階級ごとに桁が違い、500kV だけ赤くなって
 * 「その線が混んでいるか」が読めない。比率なら階級をまたいで比較できる。
 * ⚠️ 容量は公開されていないため概算（publish 層 LINE_CAPACITY_MW）。
 *
 * ★ 色標そのものは `flowGlow.ts` の `UTIL_RAMP` が唯一の定義。
 *   ここは式の形に直すだけ（凡例のグラデーションも同じ配列から作る）。
 */
function utilColor(i: number): unknown[] {
  return [
    'interpolate', ['linear'], ['coalesce', utilAt(i), 0],
    ...UTIL_RAMP.flat(),
  ]
}

/**
 * 読み込んだ GeoJSON から、そのファイルを使うレイヤーの件数を数える。
 *
 * `LayerDef.count` が `{ prop, values }` を宣言しているレイヤーについて、
 * そのプロパティの値が `values` のどれかに一致する feature を数えて
 * `{ レイヤー id: 件数 }` を返す。
 *
 * ⛔ 件数をラベルに直接書かないこと（2026-08-07）。上流が更新されると
 *    数字だけが古いまま残り、しかも誰も気づけない。
 */
function tallyCounts(file: string, data: unknown): Record<string, number> {
  const feats = (data as { features?: { properties?: Record<string, unknown> }[] })
    .features ?? []
  const out: Record<string, number> = {}
  for (const def of LAYERS) {
    if (def.file !== file || !def.count) continue
    const { prop, values } = def.count
    out[def.id] = feats.filter((f) =>
      values.includes(String(f.properties?.[prop] ?? 'unknown'))).length
  }
  return out
}

async function syncLayers(
  m: mapboxgl.Map,
  enabled: Set<string>,
  loaded: React.MutableRefObject<Set<string>>,
  pending: React.MutableRefObject<Set<string>>,
  onLoadingChange: (id: string, busy: boolean) => void,
  onPick: (f: PickedFeature | null) => void,
  onCounts: (c: Record<string, number>) => void,
  slotIdx: number,
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
        // ★ 件数は**読み込んだ実データから数える**。宣言に焼き込むと
        //   上流が更新されたとき黙って古い数字を出し続ける。
        onCounts(tallyCounts(def.file, data))
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
          // 扇形は宣言側の静的な `load` ではなく**今のコマ**で描く。
          // ここで上書きしないと、初回だけ最新コマ固定になる
          if (def.id === 'plants' && slotIdx >= 0) {
            m.setLayoutProperty(def.id, 'icon-image', pieIconExpr(slotIdx) as never)
            m.setFilter(def.id, plantsFilter(slotIdx) as never)
          }
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
  // ⚠️ コマが未確定（-1）のうちは作らない。`['at', -1, …]` は範囲外で
  //    式の評価が落ち、10 レイヤーぜんぶ addLayer に失敗する。
  //    `flow_stamps.json` は非同期に読むので、style.load の時点では
  //    まだ届いていないことがある（このパスは実際に通る）。
  if (slotIdx >= 0 && m.getLayer('flow_lines') && !m.getLayer(GLOW_TIERS[0].id)) {
    for (const t of GLOW_TIERS) {
      try {
        m.addLayer({
          id: t.id,
          type: 'line',
          // ⚠️ source は**ファイル名**で作られる（同じ GeoJSON を複数レイヤーで
          //    共有するため）。レイヤー id ではないので間違えると
          //    addLayer が例外を投げ、潮流ごと消える（実際に消えた）。
          source: 'flow_lines.geojson',
          // ★ 電圧帯 **かつ** そのコマの潮流の向きで絞る。
          //   向きごとにレイヤーが分かれている理由は flowGlow.ts の注記参照。
          filter: glowFilter(t, slotIdx) as never,
          paint: {
            'line-color': utilColor(slotIdx) as never,
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
