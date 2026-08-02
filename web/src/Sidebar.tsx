/**
 * 左サイドバー。中身は全部 `layers.ts` の宣言から生成される。
 * レイヤーを増やしてもこのファイルは変更不要。
 */
import { LAYERS, GROUP_ORDER } from './layers'
import { CapacityLegend } from './CapacityLegend'

type Props = {
  enabled: Set<string>
  loading: Set<string>
  onToggle: (id: string) => void
}

export function Sidebar({ enabled, loading, onToggle }: Props) {
  const groups = GROUP_ORDER.map((g) => ({
    name: g,
    layers: LAYERS.filter((l) => l.group === g),
  })).filter((g) => g.layers.length > 0)

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
            {g.layers.map((l) => {
              const on = enabled.has(l.id)
              return (
                <div key={l.id} className={`layer ${on ? 'on' : ''}`}>
                  <label className="row">
                    <input type="checkbox" checked={on} onChange={() => onToggle(l.id)} />
                    <span className="label">{l.label}</span>
                    {loading.has(l.id) && <span className="spinner" aria-label="読込中" />}
                  </label>
                  {l.note && <p className="note">{l.note}</p>}
                  {on && l.legend && (
                    <ul className="legend">
                      {l.legend.map((item) => (
                        <li key={item.label}>
                          <i style={{ background: item.color }} />
                          {item.label}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )
            })}
            {/* 発電レイヤーは全て「円の大きさ＝容量」で描くので、
                個々のレイヤーではなくグループの凡例として 1 つ置く */}
            {g.name === '発電' && g.layers.some((l) => enabled.has(l.id)) && (
              <CapacityLegend />
            )}
          </section>
        ))}
      </div>

      <footer className="attrib">
        <p>
          <strong>データ品質</strong>：実測＝白フチの塗りつぶし、
          <em>推算＝中空の円</em>。潮流の経路はグラフ求解であり公式線路図ではない。
        </p>
        <p className="muted">
          OCCTO / AJG (OpenStreetMap, ODbL) / GEM / 国土数値情報
        </p>
      </footer>
    </aside>
  )
}
