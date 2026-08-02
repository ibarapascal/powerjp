/**
 * 画面全体のレイアウト。左サイドバー + 地図 + 選択要素パネル。
 *
 * レイヤーの追加は `layers.ts` の LAYERS に 1 項目足すだけ。
 * このファイルは触らなくてよい。
 */
import { useCallback, useMemo, useState } from 'react'
import { MapView } from './MapView'
import { Sidebar } from './Sidebar'
import { FeaturePanel } from './FeaturePanel'
import { LAYERS } from './layers'
import type { PickedFeature } from './types'

export default function App() {
  const [enabled, setEnabled] = useState<Set<string>>(
    () => new Set(LAYERS.filter((l) => l.defaultOn).map((l) => l.id)),
  )
  const [picked, setPicked] = useState<PickedFeature | null>(null)
  const [loading, setLoading] = useState<Set<string>>(new Set())

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
        />
        {picked && <FeaturePanel picked={picked} onClose={() => setPicked(null)} />}
      </main>
    </div>
  )
}
