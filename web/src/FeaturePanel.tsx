/** 地図上の要素をクリックしたときの情報パネル。表示項目は layers.ts の popupFields で決まる。 */
import type { PickedFeature } from './types'

export function FeaturePanel({ picked, onClose }: { picked: PickedFeature; onClose: () => void }) {
  const { layer, props } = picked
  const fields = layer.popupFields ?? Object.keys(props).map((k) => ({ key: k, label: k }))
  const quality = String(props.data_quality ?? '')

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
      <dl>
        {fields.map(({ key, label }) => {
          const v = props[key]
          if (v === null || v === undefined || v === '') return null
          return (
            <div key={key} className="kv">
              <dt>{label}</dt>
              <dd>{typeof v === 'number' ? v.toLocaleString('ja-JP') : String(v)}</dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
