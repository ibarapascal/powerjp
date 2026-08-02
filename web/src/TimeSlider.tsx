/**
 * 潮流の時刻スライダー。直近 72 時間を 30 分刻みで遡る。
 *
 * ## ⚠️ 自動再生はしない
 *
 * ユーザー方針（2026-08-02）：「時系列再生は要らない。windy のように
 * 既定は最新、そこから手で戻せればよい」。**勝手に動く UI を作らない。**
 *
 * 初期位置は**最後に実測値があるコマ**。データは 30 分ごとに更新されるので、
 * 何もしなければ常に最新断面が映っている状態になる。
 */
type Props = {
  stamps: string[]
  index: number
  onChange: (i: number) => void
}

/** `20260731 14:30` → `7/31 14:30`（今日なら `14:30`） */
function label(stamp: string | undefined): string {
  if (!stamp) return '—'
  const [d, hm] = stamp.split(' ')
  if (!d || !hm) return stamp
  const mo = Number(d.slice(4, 6))
  const da = Number(d.slice(6, 8))
  return `${mo}/${da} ${hm}`
}

export function TimeSlider({ stamps, index, onChange }: Props) {
  if (!stamps.length) return null
  const last = stamps.length - 1
  const isLatest = index >= last
  // 目盛りは日付が変わるコマ（00:00）だけ。144 個すべてに線を引くと潰れる
  const ticks = stamps
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => s.endsWith(' 00:00'))

  return (
    <div className="timebar">
      <div className="timebar-head">
        <span className="timebar-now">{label(stamps[index])}</span>
        {isLatest ? (
          <span className="timebar-tag live">最新</span>
        ) : (
          <button className="timebar-tag" onClick={() => onChange(last)}>
            最新へ戻す
          </button>
        )}
        <span className="timebar-range muted">
          {label(stamps[0])} 〜 {label(stamps[last])}（30 分刻み）
        </span>
      </div>
      <div className="timebar-track">
        <input
          type="range"
          min={0}
          max={last}
          step={1}
          value={index}
          onChange={(e) => onChange(Number(e.currentTarget.value))}
          aria-label="潮流の時刻"
        />
        <div className="timebar-ticks">
          {ticks.map(({ s, i }) => (
            <span key={s} style={{ left: `${(i / last) * 100}%` }}>
              {label(s).split(' ')[0]}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
