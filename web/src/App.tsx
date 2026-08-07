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
import { useManifest, dataBase } from './manifest'
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
  // ★ dynamic 層が更新されたら version が進む。データ再取得の引き金
  const { manifest, version } = useManifest()

  // 時刻ラベルは GeoJSON とは別ファイル（全 feature 共通なので重複させない）
  // ⚠️ `version` に追従させる —— 30 分ごとに窓が 1 コマずれるので、
  //    ここを取り直さないと `slots_mw` と下標が食い違う（値が静かにずれる）。
  useEffect(() => {
    fetch(`${dataBase()}/flow_stamps.json`, { cache: 'no-cache' })
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
        setSlotIdx((prev) => {
          // ⚠️ 更新のたびに既定へ戻さない。ユーザーが手で動かした位置は保つ。
          //    ただし窓がずれて範囲外になったら既定へ寄せる
          if (prev < 0 || prev >= s.length) return u >= 0 && u < s.length ? u : s.length - 1
          return prev
        })
      })
      .catch((err) => console.error('[powerjp] flow_stamps.json の読込に失敗:', err))
  }, [version])

  // ★ 選んだ要素の時系列。潮流なら潮流、機組なら出力。
  //   ⚠️ 変電所のように時系列を持たない要素では出さない
  const chart = useMemo(() => {
    if (!picked) return null
    const series = parseSlots(picked.props.slots_mw)
    if (!series) return null
    // ⚠️ 長さが時間軸と違う＝**別のビルドの成果物が混ざっている**。
    //    窓は 30 分ごとにずれるので、下標が 1 つずれるだけで
    //    「13:00 と表示して 12:30 の値を描く」という静かな誤りになる。
    if (stamps.length && series.length !== stamps.length) return null
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
  }, [picked, slotIdx, stamps.length])

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
          slotLen={stamps.length}
          manifest={manifest}
          dataVersion={version}
          slotIdx={slotIdx}
        />
        {/* ⚠️ 潮流だけの持ち物にしない。機組の扇形も同じ時間軸で動くので、
            潮流を消していても機組が出ていればタイムバーは要る。
            ⛔ `enabled.has('flow_lines')` だけに戻さないこと */}
        {(enabled.has('flow_lines') || enabled.has('plants')) && slotIdx >= 0 && (
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
