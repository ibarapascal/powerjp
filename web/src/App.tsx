/**
 * 画面全体のレイアウト。左サイドバー + 地図 + 選択要素パネル。
 *
 * レイヤーの追加は `layers.ts` の LAYERS に 1 項目足すだけ。
 * このファイルは触らなくてよい。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { MapView } from './MapView'
import { Sidebar } from './Sidebar'
import { FeaturePanel } from './FeaturePanel'
import { TimeSlider } from './TimeSlider'
import { SlotChart } from './SlotChart'
import { parseSlots } from './slotSeries'
import { LAYERS } from './layers'
import type { PickedFeature } from './types'

export default function App() {
  const [enabled, setEnabled] = useState<Set<string>>(
    () => new Set(LAYERS.filter((l) => l.defaultOn).map((l) => l.id)),
  )
  const [picked, setPicked] = useState<PickedFeature | null>(null)
  const [loading, setLoading] = useState<Set<string>>(new Set())
  /** 実データから数えた件数（レイヤー id → 件数）。宣言に焼き込まない */
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [stamps, setStamps] = useState<string[]>([])
  // ⚠️ 初期値は -1（未確定）。stamps を読むまでコマ番号は決められない。
  //    0 を入れると一瞬 72 時間前が映ってから最新に飛ぶ。
  const [slotIdx, setSlotIdx] = useState(-1)
  /** 機組の実測が入っている最後のコマ。これより後ろは調整力が出ない（T+1 公表） */
  const [unitLastIdx, setUnitLastIdx] = useState(-1)

  // 時刻ラベルは GeoJSON とは別ファイル（全 feature 共通なので重複させない）
  useEffect(() => {
    const base = (import.meta.env.VITE_DATA_BASE as string | undefined) ?? '/data'
    fetch(`${base.replace(/\/$/, '')}/flow_stamps.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { stamps?: string[]; unit_last_idx?: number }) => {
        const s = d.stamps ?? []
        setStamps(s)
        // publish が出すのは**事実**（機組がどこまであるか）。
        // 既定コマをどこに置くかはこちらの判断。
        const u = d.unit_last_idx ?? -1
        setUnitLastIdx(u)
        // ★ 既定は「潮流も機組も値があるコマ」＝ 機組の最後。
        //   ⚠️ 末尾ではない —— `occto_unit` は T+1 公表なので、末尾にすると
        //   潮流だけ出て発電所が 1 つも出ない画面が既定になる。
        setSlotIdx(u >= 0 && u < s.length ? u : s.length - 1)
      })
      .catch((err) => console.error('[powerjp] flow_stamps.json の読込に失敗:', err))
  }, [])

  // ★ 選んだ要素の時系列。潮流なら潮流、機組なら出力。
  //   ⚠️ 変電所のように時系列を持たない要素では出さない
  const chart = useMemo(() => {
    if (!picked) return null
    const series = parseSlots(picked.props.slots_mw)
    if (!series) return null
    // 潮流は from→to の基準方向つき（符号あり）、機組は出力（符号なし）
    const isFlow = Boolean(picked.props.from && picked.props.to)
    const name = String(picked.props.line ?? picked.props.name ?? '')
    return (
      <SlotChart
        series={series}
        index={slotIdx}
        label={`${name}　${isFlow ? '潮流' : '出力'}`}
      />
    )
  }, [picked, slotIdx])

  const mergeCounts = useCallback((c: Record<string, number>) => {
    setCounts((prev) => ({ ...prev, ...c }))
  }, [])

  /** 親 checkbox 用。子をまとめて ON/OFF する（一部 ON なら全 ON に倒す） */
  const toggleMany = useCallback((ids: string[], on: boolean) => {
    setEnabled((prev) => {
      const next = new Set(prev)
      for (const id of ids) on ? next.add(id) : next.delete(id)
      return next
    })
  }, [])

  const toggle = useCallback((id: string) => {
    setEnabled((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  const onLoadingChange = useCallback((id: string, busy: boolean) => {
    setLoading((prev) => {
      const next = new Set(prev)
      busy ? next.add(id) : next.delete(id)
      return next
    })
  }, [])

  const token = useMemo(() => import.meta.env.VITE_MAPBOX_TOKEN as string | undefined, [])

  if (!token || token.startsWith('pk.xxx')) {
    return (
      <div className="setup">
        <h1>powerjp</h1>
        <p>
          Mapbox のアクセストークンが未設定です。<code>web/.env</code> を作成して
          以下を記入してください：
        </p>
        <pre>VITE_MAPBOX_TOKEN=pk.あなたのトークン</pre>
        <p className="muted">
          トークンは <a href="https://account.mapbox.com/access-tokens/">account.mapbox.com</a> で取得できます。
          <br />
          <code>.env</code> は gitignore 済みです。
        </p>
      </div>
    )
  }

  return (
    <div className="app">
      <Sidebar enabled={enabled} loading={loading} counts={counts}
               onToggle={toggle} onToggleMany={toggleMany} />
      {/* ★ ドロワーは `.app` 直下に置く。`.map-wrap` の中だと
          left:0 が「地図の左端」＝サイドバーの右隣になってしまう。
          サイドバーに**重ねる**のが要求（2026-08-07）。 */}
      {picked && (
        <FeaturePanel picked={picked} slotIdx={slotIdx} stamps={stamps}
                      onClose={() => setPicked(null)} />
      )}
      <main className="map-wrap">
        <MapView
          token={token}
          enabled={enabled}
          onPick={setPicked}
          onLoadingChange={onLoadingChange}
          onCounts={mergeCounts}
          slotIdx={slotIdx}
        />
        {enabled.has('flow_lines') && slotIdx >= 0 && (
          <TimeSlider
            stamps={stamps}
            index={slotIdx}
            unitLastIdx={unitLastIdx}
            onChange={setSlotIdx}
            chart={chart}
          />
        )}
      </main>
    </div>
  )
}
