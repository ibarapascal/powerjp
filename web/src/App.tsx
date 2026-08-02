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
import { LAYERS } from './layers'
import type { PickedFeature } from './types'

export default function App() {
  const [enabled, setEnabled] = useState<Set<string>>(
    () => new Set(LAYERS.filter((l) => l.defaultOn).map((l) => l.id)),
  )
  const [picked, setPicked] = useState<PickedFeature | null>(null)
  const [loading, setLoading] = useState<Set<string>>(new Set())
  const [stamps, setStamps] = useState<string[]>([])
  // ⚠️ 初期値は -1（未確定）。stamps を読むまでコマ番号は決められない。
  //    0 を入れると一瞬 72 時間前が映ってから最新に飛ぶ。
  const [slotIdx, setSlotIdx] = useState(-1)

  // 時刻ラベルは GeoJSON とは別ファイル（全 feature 共通なので重複させない）
  useEffect(() => {
    const base = (import.meta.env.VITE_DATA_BASE as string | undefined) ?? '/data'
    fetch(`${base.replace(/\/$/, '')}/flow_stamps.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { stamps?: string[] }) => {
        const s = d.stamps ?? []
        setStamps(s)
        setSlotIdx(s.length - 1)      // ★ 既定は最新コマ
      })
      .catch((err) => console.error('[powerjp] flow_stamps.json の読込に失敗:', err))
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
      <Sidebar enabled={enabled} loading={loading} onToggle={toggle} />
      <main className="map-wrap">
        <MapView
          token={token}
          enabled={enabled}
          onPick={setPicked}
          onLoadingChange={onLoadingChange}
          slotIdx={slotIdx}
        />
        {picked && (
          <FeaturePanel picked={picked} slotIdx={slotIdx} stamps={stamps}
                        onClose={() => setPicked(null)} />
        )}
        {enabled.has('flow_lines') && slotIdx >= 0 && (
          <TimeSlider stamps={stamps} index={slotIdx} onChange={setSlotIdx} />
        )}
      </main>
    </div>
  )
}
