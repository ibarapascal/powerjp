/**
 * 左サイドバー。中身は全部 `layers.ts` の宣言から生成される。
 * レイヤーを増やしてもこのファイルは変更不要。
 *
 * ## ⛔ ON/OFF で出し入れしない
 *
 * 説明も凡例も、checkbox の状態に関わらず**常に出す**（ユーザー要求 2026-08-07
 * 「不要让画面布局闪烁」）。切り替えるたびに高さが変わると、下のレイヤーの
 * checkbox が動いて押し間違える。
 *
 * ## 入れ子（親 → 種別）
 *
 * `LayerDef.parent` を持つレイヤーは `PARENTS` の下にまとめて畳む。
 * 平らに 8 個並べるとサイドバーが変電所だけで埋まるため。
 * 親の checkbox は子の全 ON / 一部 ON（indeterminate）/ 全 OFF を映す。
 */
import { useState } from 'react'
import { LAYERS, GROUP_ORDER, GROUP_LEGENDS, PARENTS } from './layers'
import type { LayerDef } from './layers'

type Props = {
  enabled: Set<string>
  loading: Set<string>
  /** 実データから数えた件数（レイヤー id → 件数） */
  counts: Record<string, number>
  onToggle: (id: string) => void
  onToggleMany: (ids: string[], on: boolean) => void
}

export function Sidebar({ enabled, loading, counts, onToggle, onToggleMany }: Props) {
  // 既定は**展開**（ユーザー要求 2026-08-07）。閉じた状態を覚えるより
  // 「中身が見えている」ことを優先する。畳むのは手で ▾ を押したとき
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(PARENTS.map((p) => p.id)),
  )

  const groups = GROUP_ORDER.map((g) => ({
    name: g,
    // 親を持つレイヤーは親の下に出すので、ここでは除く
    layers: LAYERS.filter((l) => l.group === g && !l.parent),
    parents: PARENTS.filter((p) => p.group === g),
  })).filter((g) => g.layers.length > 0 || g.parents.length > 0)

  /** 1 レイヤー分の行（checkbox・説明・凡例） */
  const renderLayer = (l: LayerDef, nested = false) => {
    const on = enabled.has(l.id)
    const n = counts[l.id]
    return (
      <div key={l.id} className={`layer ${on ? 'on' : ''} ${nested ? 'nested' : ''}`}>
        <label className="row">
          <input type="checkbox" checked={on} onChange={() => onToggle(l.id)} />
          <span className="label">{l.label}</span>
          {/* 件数は実データ由来。読み込み前は出さない（宣言に焼き込まない） */}
          {n !== undefined && <span className="count">{n.toLocaleString()}</span>}
          {loading.has(l.id) && <span className="spinner" aria-label="読込中" />}
        </label>
        {l.note && <p className="note">{l.note}</p>}
        {l.legend && (
          <ul className="legend">
            {l.legend.map((item) => (
              <li key={item.label}>
                <i style={{ background: item.color }} />
                {item.label}
              </li>
            ))}
          </ul>
        )}
        {l.legendGradient && (
          <div className="legend-grad">
            <div className="bar" style={{ background: l.legendGradient.css }} />
            <div className="ticks">
              {l.legendGradient.ticks.map((t) => <span key={t}>{t}</span>)}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <aside className="sidebar">
      <header className="brand">
        <h1>powerjp</h1>
        <p>日本の電力系統 — 公開データによる再構成</p>
      </header>

      <div className="groups">
        {groups.map((g) => (
          <section key={g.name} className="group">
            <h2>{g.name}</h2>

            {g.layers.map((l) => renderLayer(l))}

            {g.parents.map((p) => {
              const kids = LAYERS.filter((l) => l.parent === p.id)
              const onCount = kids.filter((l) => enabled.has(l.id)).length
              const all = onCount === kids.length
              const some = onCount > 0 && !all
              const total = kids.reduce(
                (a, l) => a + (counts[l.id] ?? 0), 0)
              const isOpen = open.has(p.id)
              return (
                <div key={p.id} className={`layer parent ${onCount ? 'on' : ''}`}>
                  <label className="row">
                    <input
                      type="checkbox"
                      checked={all}
                      // 一部だけ ON のときは indeterminate（三態）で示す
                      ref={(el) => { if (el) el.indeterminate = some }}
                      onChange={() => onToggleMany(kids.map((k) => k.id), !all)}
                    />
                    <span className="label">{p.label}</span>
                    {total > 0 && <span className="count">{total.toLocaleString()}</span>}
                    <button
                      type="button"
                      className="disclose"
                      aria-expanded={isOpen}
                      aria-label={isOpen ? '種別を畳む' : '種別を開く'}
                      onClick={(e) => {
                        e.preventDefault()
                        setOpen((prev) => {
                          const next = new Set(prev)
                          next.has(p.id) ? next.delete(p.id) : next.add(p.id)
                          return next
                        })
                      }}
                    >
                      {isOpen ? '▾' : '▸'}
                    </button>
                  </label>
                  {p.note && <p className="note">{p.note}</p>}
                  {isOpen && (
                    <div className="children">
                      {kids.map((l) => renderLayer(l, true))}
                    </div>
                  )}
                </div>
              )
            })}

            {/* グループで共通の凡例は、レイヤーごとに繰り返さずここで 1 回だけ。
                ⛔ ON/OFF で出し入れしない（レイアウトを揺らさない） */}
            {GROUP_LEGENDS[g.name] && (
              <ul className="legend group-legend">
                {GROUP_LEGENDS[g.name].map((item) => (
                  <li key={item.label}>
                    <i style={{ background: item.color }} />
                    {item.label}
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>

      {/* ⚠️ 出典の 1 行は消せない —— AJG/OSM は ODbL、OCCTO は出典明記が条件。
          地図の右下から customAttribution を外したぶん、ここが唯一の表記になる。 */}
      <footer className="attrib">
        <p className="muted">OCCTO / AJG (OSM, ODbL) / GEM / 国土数値情報</p>
      </footer>
    </aside>
  )
}
