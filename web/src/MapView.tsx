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
import { registerPieIcons, pieIconExpr } from './pieIcons'
import {
  GLOW_TIERS, GLOW_SOURCE_ID, glowFilter, utilAt, UTIL_RAMP,
  startFlowAnimation, stopFlowAnimation, holdFlowAnimation,
} from './flowGlow'
import type { PickedFeature } from './types'
import { dataUrl, fileStamps } from './manifest'
import type { Manifest } from './manifest'

type Props = {
  token: string
  enabled: Set<string>
  onPick: (f: PickedFeature | null) => void
  onLoadingChange: (id: string, busy: boolean) => void
  /** 実データから数えた件数（レイヤー id → 件数）を返す */
  onCounts: (c: Record<string, number>) => void
  /** 表示するコマ（`flow_stamps.json` の添字）。潮流の向きも色もこれで決まる */
  slotIdx: number
  /** 時間軸のコマ数。成果物の世代ずれ検出に使う */
  slotLen: number
  /** どのファイルが dynamic 層かの判定に使う */
  manifest: Manifest | null
  /** dynamic が更新されるたびに増える。増えたら該当 source を取り直す */
  dataVersion: number
}

// 初期表示は首都圏。全国からだと線が細く潮流も判別できないので、
// 「まず何が見えるか」が伝わる縮尺で開く。全国は引けば見える。
const INITIAL_CENTER: [number, number] = [139.75, 35.75]   // 東京
const INITIAL_ZOOM = 8.2
// flow は 10 層 × filter/color を同時に更新するため、地図側だけ約 30fps に
// 制限する。時間軸と機組の React 表示は入力どおり更新し、地図は常に最新コマへ
// 追いつく。30fps は 30 分刻みの比較操作には十分で、style diff の滞留を半減できる。
const MIN_FLOW_SLOT_INTERVAL_MS = 32

export function MapView({
  token, enabled, onPick, onLoadingChange, onCounts, slotIdx, slotLen,
  manifest, dataVersion,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const map = useRef<mapboxgl.Map | null>(null)
  const ready = useRef(false)
  /** syncLayers は非同期なので、レイヤー生成時に最新のコマを読めるよう ref で持つ */
  const slot = useRef(slotIdx)
  slot.current = slotIdx
  /** ドラッグ中の連続イベントを 1 フレーム 1 回の地図更新へ畳み込む */
  const pendingSlot = useRef(-1)
  const slotUpdateRaf = useRef<number | null>(null)
  const appliedSlot = useRef(-1)
  const lastFlowSlotUpdateAt = useRef(0)
  /** 既に fetch 済みの layer id。再取得を避けるため消さない */
  const loaded = useRef<Set<string>>(new Set())
  const pending = useRef<Set<string>>(new Set())
  /** ファイル名 → 取得した時点の mtime。これと manifest を比べて差分だけ取り直す */
  const fetchedAt = useRef<Record<string, number>>({})

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
      // ⚠️ **dev だけ**。本番では要らないうえ、GPU メモリを余分に使う
      //    （検証のための設定であって、利用者のための設定ではない）。
      preserveDrawingBuffer: import.meta.env.DEV,
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
      // ★ 何も無いところをクリックしたら選択を解除する（要求 2026-08-07）。
      //   ⚠️ レイヤーごとの click ハンドラとは別に、地図全体でも受ける。
      //   当たり判定を自分で取り直し、**どのレイヤーにも当たらなかったときだけ**
      //   閉じる —— そうしないと要素を選んだ直後に自分で解除してしまう。
      m.on('click', (e) => {
        const ids = LAYERS.map((d) => d.id).filter((id) => m.getLayer(id))
        if (!ids.length) return
        if (!m.queryRenderedFeatures(e.point, { layers: ids }).length) onPick(null)
      })
      ready.current = true
      syncLayers(m, enabled, loaded, pending, onLoadingChange, onPick, onCounts,
                 slot, slotLen, manifest, fetchedAt)
    })
    map.current = m
    return () => {
      if (slotUpdateRaf.current != null) {
        cancelAnimationFrame(slotUpdateRaf.current)
        slotUpdateRaf.current = null
      }
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
    syncLayers(m, enabled, loaded, pending, onLoadingChange, onPick, onCounts,
               slot, slotLen, manifest, fetchedAt)
  }, [enabled, onLoadingChange, onPick, onCounts, slotLen, manifest])

  // ── 配信物の更新に追従（30 分ごとの再配信・static の差し替え）──
  //
  // ⚠️ ここは `setData` を使ってよい。避けるべきなのは**スライダーを引くたび**の
  //    再パース（LESSONS #40）であって、更新のたびなら問題にならない。
  //    ⛔ source を作り直さないこと —— レイヤーが参照を失う。
  // ⚠️ 層で分けず**ファイルごとの mtime** で判定する。static も変わりうる
  //    （AJG の新版、配信を保留した層の変更など）。
  useEffect(() => {
    const m = map.current
    if (!m || !ready.current || dataVersion === 0) return
    const stamps = fileStamps(manifest)
    let alive = true
    ;(async () => {
      const files = [...new Set(
        LAYERS.filter((d) => loaded.current.has(d.id)).map((d) => d.file),
      )].filter((f) => stamps[f] && stamps[f] !== fetchedAt.current[f])
      for (const file of files) {
        try {
          const res = await fetch(dataUrl(file, manifest), { cache: 'no-cache' })
          if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
          const data = await res.json()
          if (!alive) return
          const src = m.getSource(file) as mapboxgl.GeoJSONSource | undefined
          if (src?.setData) {
            src.setData(data)
            // 流光の専用 source も同じ data で追随させる（片方だけ更新すると
            // 潮流線と光で別世代のコマを描くことになる）
            if (file === 'flow_lines.geojson') {
              const glowSrc = m.getSource(GLOW_SOURCE_ID) as
                mapboxgl.GeoJSONSource | undefined
              glowSrc?.setData?.(data)
            }
            fetchedAt.current[file] = stamps[file]
            onCounts(tallyCounts(file, data))
          }
        } catch (err) {
          console.error(`[powerjp] ${file} の再取得に失敗:`, err)
        }
      }
    })()
    return () => { alive = false }
  }, [dataVersion, manifest, onCounts])

  // ── コマの移動に追従 ──
  //
  // ⚠️ ここで `setData` を呼んではいけない。1.5MB の GeoJSON を再パースして
  //    タイルを作り直すので、スライダーを引くと固まる。
  //    `setFilter` / `setPaintProperty` は幾何に触らないので軽い。
  //    ただし Chrome の input イベントは同一フレームに複数届くことがある。
  //    ここで毎回 10 層 × 2 プロパティを書き換えると、Mapbox の style diff が
  //    滞留して Windows/ANGLE で特に詰まりやすいので、最新コマだけ描く。
  useEffect(() => {
    pendingSlot.current = slotIdx
    if (slotIdx < 0 || slotUpdateRaf.current != null) return

    const applyPendingSlot = () => {
      slotUpdateRaf.current = null
      const m = map.current
      const idx = pendingSlot.current
      if (!m || !ready.current || idx < 0 || idx === appliedSlot.current) return

      const flowOn = enabled.has('flow_lines')
      const now = performance.now()
      const wait = MIN_FLOW_SLOT_INTERVAL_MS - (now - lastFlowSlotUpdateAt.current)
      if (flowOn && wait > 0) {
        slotUpdateRaf.current = requestAnimationFrame(applyPendingSlot)
        return
      }
      appliedSlot.current = idx

      if (flowOn) {
        lastFlowSlotUpdateAt.current = now
        // ★ コマを動かしている間は光を流さない。色と向きだけを変える。
        //   手が止まって 300ms で自然に再開する（`holdFlowAnimation` 参照）
        holdFlowAnimation(m, 300)
        const color = utilColor(idx) as never
        for (const t of GLOW_TIERS) {
          if (!m.getLayer(t.id)) continue
          try {
            m.setFilter(t.id, glowFilter(t, idx) as never)
            m.setPaintProperty(t.id, 'line-color', color)
          } catch (err) {
            console.error(`[powerjp] ${t.id} のコマ更新に失敗:`, err)
          }
        }
      }

      // 機組の扇形も同じコマへ。潮流と別の時刻を映すと嘘になる
      if (enabled.has('plants') && m.getLayer('plants')) {
        try {
          // ⚠️ 欠測コマも**描く**（円環だけ・破線）。隠すと「発電所が無い」に
          //    見えてしまう。区別は `pieIcons` の破線が担う
          m.setLayoutProperty('plants', 'icon-image', pieIconExpr(idx) as never)
        } catch (err) {
          console.error('[powerjp] 扇形のコマ更新に失敗:', err)
        }
      }
    }
    slotUpdateRaf.current = requestAnimationFrame(applyPendingSlot)
  }, [slotIdx, enabled])

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

/**
 * `slots_mw` の長さが時間軸と食い違っていないか見る。
 * ⛔ 黙って通さない —— これは「描画が壊れる」のではなく
 *    「**正しい見た目で間違った値**」になる種類の不整合。
 */
function warnIfSlotLenMismatch(file: string, data: unknown, expected: number) {
  if (!expected) return
  const f = (data as { features?: { properties?: Record<string, unknown> }[] })
    .features?.[0]
  const raw = f?.properties?.slots_mw
  if (raw === undefined) return
  const len = Array.isArray(raw)
    ? raw.length
    : (() => { try { return JSON.parse(String(raw)).length } catch { return -1 } })()
  if (len > 0 && len !== expected) {
    console.error(
      `[powerjp] ${file} の slots_mw が ${len} コマ、時間軸は ${expected} コマ。`
      + '配信中の世代がずれている可能性がある（値が静かにずれる）。再読込を。',
    )
  }
}

async function syncLayers(
  m: mapboxgl.Map,
  enabled: Set<string>,
  loaded: React.MutableRefObject<Set<string>>,
  pending: React.MutableRefObject<Set<string>>,
  onLoadingChange: (id: string, busy: boolean) => void,
  onPick: (f: PickedFeature | null) => void,
  onCounts: (c: Record<string, number>) => void,
  slot: React.MutableRefObject<number>,
  slotLen: number,
  /** 取得時点の mtime を控えるため。更新判定の基準になる */
  manifest: Manifest | null,
  fetchedAt: React.MutableRefObject<Record<string, number>>,
) {
  // 生产 CDN 只公开哈希对象；manifest 到达前不能猜稳定文件名。
  // dev 没有 VITE_DATA_BASE 时仍允许直接读 /data，方便本地看 publish 快照。
  if (import.meta.env.VITE_DATA_BASE && !manifest) return
  const stamps = fileStamps(manifest)
  for (const def of LAYERS) {
    const want = enabled.has(def.id)

    if (want && !loaded.current.has(def.id)) {
      if (pending.current.has(def.id)) continue
      pending.current.add(def.id)
      onLoadingChange(def.id, true)
      try {
        // 本番は CDN（VITE_DATA_BASE）、dev は vite プラグインが ../storage/publish を配信
        const res = await fetch(dataUrl(def.file, manifest))
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const data = await res.json()
        // ⚠️ **成果物の世代ずれを検出する。**
        //    publish は 30 分ごとに窓を 1 コマずらすので、
        //    `flow_lines`（新）と `flow_stamps`（旧）が混ざると
        //    下標が全部ずれ、**画面は正常なまま値だけ間違う**。
        //    R2 へのアップロード中に読まれると起こりうる（原子性は
        //    manifest 前提だが、前端はまだ manifest を見ていない）。
        warnIfSlotLenMismatch(def.file, data, slotLen)
        // ★ source は **file 単位**。同じ GeoJSON を複数レイヤーで
        //   filter 違いで使い分ける（送電線を 3 レイヤーに分けている）。
        //   layer ごとに source を作ると同じデータを何度も持つことになる。
        const srcId = def.file
        if (!m.getSource(srcId)) m.addSource(srcId, { type: 'geojson', data })
        // ★ 流光は**専用 source**（中身は同じ data）。dasharray/filter の
        //   書き込みは source 単位の全タイル再レイアウトなので、共有すると
        //   潮流線本体まで一緒に作り直されてちらつく（flowGlow.ts 参照）。
        if (def.file === 'flow_lines.geojson' && !m.getSource(GLOW_SOURCE_ID)) {
          m.addSource(GLOW_SOURCE_ID, { type: 'geojson', data })
        }
        // ★ 件数は**読み込んだ実データから数える**。宣言に焼き込むと
        //   上流が更新されたとき黙って古い数字を出し続ける。
        onCounts(tallyCounts(def.file, data))
        // ★ 取得時点の mtime を控える。更新判定の基準になる
        fetchedAt.current[def.file] = stamps[def.file] ?? 0
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
          const currentSlot = slot.current
          if (def.id === 'plants' && currentSlot >= 0) {
            m.setLayoutProperty(def.id, 'icon-image', pieIconExpr(currentSlot) as never)
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
  const currentSlot = slot.current
  if (currentSlot >= 0 && m.getLayer('flow_lines') && !m.getLayer(GLOW_TIERS[0].id)) {
    for (const t of GLOW_TIERS) {
      try {
        m.addLayer({
          id: t.id,
          type: 'line',
          // ⚠️ 流光は専用 source（潮流線本体と共有しない —— flowGlow.ts 参照）。
          //    source id を間違えると addLayer が例外を投げ、潮流ごと消える。
          source: GLOW_SOURCE_ID,
          // ★ 電圧帯 **かつ** そのコマの潮流の向きで絞る。
          //   向きごとにレイヤーが分かれている理由は flowGlow.ts の注記参照。
          filter: glowFilter(t, currentSlot) as never,
          paint: {
            'line-color': utilColor(currentSlot) as never,
            // 幅は定数（zoom 補間のみ）。送電線の同電圧帯と同じ値
            'line-width': ['interpolate', ['linear'], ['zoom'], 4, t.w4, 11, t.w11],
            'line-opacity': 1,
            // @types/mapbox-gl は array<number> のみだが、流光は zoom ごとの
            // `step` 式を使う（flowGlow.ts 参照）。
            'line-dasharray': t.seq[0] as never,
          },
        })
      } catch (err) {
        console.error(`[powerjp] ${t.id} の追加に失敗:`, err)
      }
    }
  }

  // OFF にした flow の RAF を残さない。再び ON にしたときはここで再開する。
  if (enabled.has('flow_lines') && m.getLayer(GLOW_TIERS[0].id)) {
    startFlowAnimation(m)
  } else if (!enabled.has('flow_lines')) {
    stopFlowAnimation(m)
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
