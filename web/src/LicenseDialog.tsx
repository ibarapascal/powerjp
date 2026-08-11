/**
 * 出典・ライセンスの全文ダイアログ。サイドバー最下部の 1 行から開く。
 *
 * ## なぜ全画面中央なのか
 *
 * 出典表示は**義務**であって補足ではない（ODbL の share-alike、OCCTO の
 * 出典明記）。サイドバーの隅に畳んでおくと読まれないし、狭くて全条件を
 * 書けない。読ませる必要があるものは、地図を一旦どかして出す。
 *
 * ⛔ 中身を要約しない。条件の全文と URL を出すことがこの画面の目的。
 */
import { useEffect } from 'react'
import { LICENSES, BASEMAP_NOTE } from './licenses'

type Props = { onClose: () => void }

export function LicenseDialog({ onClose }: Props) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  return (
    // 背景をクリックしても閉じる。⚠️ 中身のクリックは伝播させない
    <div className="lic-backdrop" onClick={onClose} role="presentation">
      <div
        className="lic"
        role="dialog"
        aria-modal="true"
        aria-label="出典とライセンス"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="lic-head">
          <h2>出典とライセンス</h2>
          <button onClick={onClose} aria-label="閉じる">×</button>
        </div>

        <p className="lic-lead">
          この地図は公開データを組み合わせて作られています。条件は出典ごとに
          異なります。
        </p>
        <p className="lic-lead lic-warn">
          配信している GeoJSON は OpenStreetMap 由来（ODbL）の幾何を含む
          <strong>派生データベース</strong>であり、<strong>ODbL のもとで提供されます</strong>。
          出典を示し、同じ条件で公開する限り、自由に利用・改変・再配布できます。
        </p>

        <ul className="lic-list">
          {LICENSES.map((l) => (
            <li key={l.name}>
              {/* ⛔ 見出しはリンクにしない。同じ行き先へのリンクが 2 つあると
                  どちらを押すか迷う。遷移先は下の URL 行に一本化する。 */}
              <div className="lic-name">
                <span>{l.name}</span>
              </div>
              <div className="lic-provider">{l.provider}</div>
              <dl>
                <div><dt>用途</dt><dd>{l.used}</dd></div>
                <div><dt>ライセンス</dt><dd>{l.licence}</dd></div>
                <div><dt>再配布</dt><dd>{l.redistribution}</dd></div>
                {l.note && <div><dt>備考</dt><dd>{l.note}</dd></div>}
                <div>
                  <dt>URL</dt>
                  <dd className="lic-url">
                    <a href={l.url} target="_blank" rel="noopener noreferrer">{l.url}</a>
                  </dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>

        <p className="lic-foot">{BASEMAP_NOTE}</p>
        <p className="lic-foot">
          コードは MIT ライセンスです。データは上記各条件に従います。
        </p>
        <p className="lic-foot">
          潮流の経路はグラフ上で求解した推定であり、公式の線路図ではありません。
          <code>data_quality: estimated</code> の値は按分による推計で、
          その地点の実測ではありません。
        </p>
      </div>
    </div>
  )
}
