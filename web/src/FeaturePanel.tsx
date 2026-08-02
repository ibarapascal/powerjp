/** 地図上の要素をクリックしたときの情報パネル。表示項目は layers.ts の popupFields で決まる。 */
import type { LayerDef } from './layers'
import type { PickedFeature } from './types'

/**
 * ⚠️ **Mapbox は feature の配列プロパティを JSON 文字列にして返す。**
 *
 * 実測（2026-08-02）：GeoJSON に `slots_mw: [10,-20,null,30]` を入れても
 * `queryRenderedFeatures()` で取れるのは `"[10,-20,null,30]"`（typeof string）。
 * **式（`['at', i, ['get','slots_mw']]`）の側では配列のまま扱えるので、
 * 描画は正しく動く。JS で読むときだけ parse が要る。**
 */
function asArray(v: unknown): (number | null)[] | null {
  if (Array.isArray(v)) return v as (number | null)[]
  if (typeof v !== 'string') return null
  try {
    const p = JSON.parse(v)
    return Array.isArray(p) ? p : null
  } catch {
    return null
  }
}

type Props = {
  picked: PickedFeature
  onClose: () => void
  /** 潮流の表示コマ。この時刻の値をパネルにも出す */
  slotIdx?: number
  stamps?: string[]
}

export function FeaturePanel({ picked, onClose, slotIdx = -1, stamps = [] }: Props) {
  const { layer, props } = picked
  const fields: NonNullable<LayerDef['popupFields']> =
    layer.popupFields ?? Object.keys(props).map((k) => ({ key: k, label: k }))
  const quality = String(props.data_quality ?? '')

  // そのコマの実測値。地図（流光の向き・扇形の角度）と必ず一致させる
  const slots = asArray(props.slots_mw)
  const mw = slots && slotIdx >= 0 ? slots[slotIdx] ?? null : null
  const cap = Number(props.capacity_mw ?? 0)
  const util = mw !== null && cap > 0 ? Math.abs(mw) / cap : null
  const frm = String(props.from ?? '')
  const to = String(props.to ?? '')
  // 潮流線は向きがある（from/to を持つ）、機組は出力だけ
  const isFlow = Boolean(frm && to)

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="tag">{layer.label}</span>
        <button onClick={onClose} aria-label="閉じる">×</button>
      </div>
      {quality === 'estimated' && (
        <p className="warn">
          ⚠️ この値は<strong>推算</strong>です。エリア実績を設備容量比で按分したもので、
          この地点の実測ではありません。
        </p>
      )}
      {slots && (
        <dl className="slotnow">
          <div className="kv">
            <dt>時刻</dt>
            <dd>{stamps[slotIdx] ?? '—'}</dd>
          </div>
          <div className="kv">
            <dt>{isFlow ? 'この時刻の潮流' : 'この時刻の出力'}</dt>
            <dd>
              {mw === null ? '欠測' : (
                <>
                  {Math.abs(mw).toLocaleString('ja-JP')} MW
                  {isFlow && (
                    <>
                      {' '}
                      <span className="muted">
                        {mw >= 0 ? `${frm} → ${to}` : `${to} → ${frm}`}
                      </span>
                    </>
                  )}
                </>
              )}
            </dd>
          </div>
          {util !== null && (
            <div className="kv">
              <dt>{isFlow ? '負荷率（推定）' : '設備利用率'}</dt>
              <dd>{(util * 100).toFixed(0)}%</dd>
            </div>
          )}
        </dl>
      )}
      <dl>
        {fields.map(({ key, label, map }) => {
          const v = props[key]
          if (v === null || v === undefined || v === '') return null
          // 生の配列は出さない（上の「このコマの潮流」で見せている）
          if (key === 'slots_mw') return null
          // 真偽値をそのまま出すと "false" になる。コード値は map で日本語へ
          const text =
            typeof v === 'boolean' ? (v ? 'はい' : 'いいえ')
              : typeof v === 'number' ? v.toLocaleString('ja-JP')
                : map?.[String(v)] ?? String(v)
          return (
            <div key={key} className="kv">
              <dt>{label}</dt>
              <dd>{text}</dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
