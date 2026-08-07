/**
 * 潮流の時刻スライダー。直近 72 時間を 30 分刻みで遡る。
 *
 * ## ⚠️ 自動再生はしない
 *
 * ユーザー方針（2026-08-02）：「時系列再生は要らない。windy のように
 * 既定は最新、そこから手で戻せればよい」。**勝手に動く UI を作らない。**
 *
 * ## 目盛りは物差し（2026-08-07）
 *
 * 日と時をそれぞれ違う高さの罫で刻む。等間隔の点ではなく**物差し**にすると、
 * 「いま何時を見ているか」「どのくらい戻したか」が数えずに読める。
 *
 *     日   ┃          ┃          ┃
 *     6h   ┃  ╷  ╷  ╷ ┃  ╷  ╷  ╷ ┃
 *     1h   ┃╷╷╷╷╷╷╷╷╷╷┃╷╷╷╷╷╷╷╷╷╷┃
 *
 * ## ⚠️ 機組が無い区間を隠さない
 *
 * `occto_unit` は T+1 公表なので、潮流に値があるコマでも機組は最大で
 * 丸一日ぶん欠測になる。そこを黙って通すと「全機停止した」ように見える。
 * トラック上に**斜線の帯**を敷いて、その区間は調整力機組が出ないと明示する。
 */
type Props = {
  stamps: string[]
  index: number
  /** 機組の実測が入っている最後のコマ。これより後ろは調整力が出ない */
  unitLastIdx: number
  onChange: (i: number) => void
}

const WDAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** `20260731` → `Thu`。曜日は平日と休日で需給の形が変わるので効く情報 */
function wday(ymd: string): string {
  const y = Number(ymd.slice(0, 4))
  const m = Number(ymd.slice(4, 6))
  const d = Number(ymd.slice(6, 8))
  return WDAY[new Date(y, m - 1, d).getDay()] ?? ''
}

/** `20260731 14:30` → `7/31 Thu 14:30` */
function label(stamp: string | undefined): string {
  if (!stamp) return '—'
  const [d, hm] = stamp.split(' ')
  if (!d || !hm) return stamp
  return `${Number(d.slice(4, 6))}/${Number(d.slice(6, 8))} ${wday(d)} ${hm}`
}

/**
 * 物差しの日付ラベル。`8/5`
 * ⛔ ここに曜日は入れない（ユーザー要求 2026-08-07）。曜日は左上の現在時刻だけ。
 *    目盛りは細かいので、1 つでも要素が増えると隣の数字と衝突する。
 */
function dayLabel(stamp: string): string {
  const d = stamp.split(' ')[0]
  return `${Number(d.slice(4, 6))}/${Number(d.slice(6, 8))}`
}

const hourOf = (s: string) => Number(s.split(' ')[1]?.slice(0, 2) ?? -1)
const isOnTheHour = (s: string) => s.endsWith(':00')

export function TimeSlider({ stamps, index, unitLastIdx, onChange }: Props) {
  if (!stamps.length) return null
  const last = stamps.length - 1
  const isLatest = index >= last
  const pct = (i: number) => (last > 0 ? (i / last) * 100 : 0)

  // 物差しの刻み。日 > 6 時間 > 3 時間 > 1 時間 の 4 段。
  // ⚠️ 数字は **3 時間ごと**に出す（2026-08-07「再细一点」）。
  //    6 時間ごとだと 1 日に 3 個しか数字が無く、目盛りとして粗すぎる。
  const ruler = stamps
    .map((s, i) => ({ s, i }))
    .filter(({ s }) => isOnTheHour(s))
    .map(({ s, i }) => {
      const h = hourOf(s)
      const kind = h === 0 ? 'day'
        : h % 6 === 0 ? 'six'
        : h % 3 === 0 ? 'three'
        : 'hour'
      return { i, h, kind: kind as 'day' | 'six' | 'three' | 'hour' }
    })
  const days = ruler.filter((t) => t.kind === 'day')

  // 機組が欠測の区間（潮流にはあるが調整力が無い）
  const hasGap = unitLastIdx >= 0 && unitLastIdx < last
  const gapFrom = hasGap ? pct(unitLastIdx) : 0

  return (
    <div className="timebar">
      <div className="timebar-head">
        <span className="timebar-now">{label(stamps[index])}</span>
        {isLatest ? (
          <span className="timebar-tag live">最新</span>
        ) : (
          <button className="timebar-tag" onClick={() => onChange(last)}>
            最新へ
          </button>
        )}
        {/* いま見ているコマに調整力が無いなら、その場で言う */}
        {hasGap && index > unitLastIdx && (
          <span className="timebar-tag nodata">調整力データなし</span>
        )}
        <span className="timebar-range">30 分刻み</span>
      </div>

      <div className="timebar-track">
        {/* 機組が無い区間。斜線で「データが無い」を示す（0 ではない） */}
        {hasGap && (
          <div
            className="timebar-gap"
            style={{ left: `${gapFrom}%`, right: 0 }}
            title="この区間は潮流のみ。調整力機組は T+1 公表のためまだ無い"
          />
        )}
        <input
          type="range"
          min={0}
          max={last}
          step={1}
          value={index}
          onChange={(e) => onChange(Number(e.currentTarget.value))}
          aria-label="潮流の時刻"
        />
        {/* ★ 現在位置の指針。つまみ自体は透明にして、見た目はこちらで描く。
            ⚠️ 縦線を物差しまで下ろすのが要点 —— 丸だけだと「いま何時か」を
            目盛りと突き合わせられない。 */}
        <div className="timebar-cursor" style={{ left: `${pct(index)}%` }}>
          <span className="pulse" />
        </div>
        {/* 物差しの罫 */}
        <div className="timebar-ruler">
          {ruler.map((t) => (
            <i key={t.i} className={t.kind} style={{ left: `${pct(t.i)}%` }} />
          ))}
        </div>
        {/* 3 時間ごとの時刻と、日付 */}
        <div className="timebar-ticks">
          {ruler
            // ⚠️ 0 時（kind='day'）も数字を出す。日付だけだと 00 が抜けて
            //    「03 から始まる」ように見え、目盛りの連なりが切れる
            .filter((t) => t.kind !== 'hour')
            .map((t) => (
              <span key={t.i} className="hour" style={{ left: `${pct(t.i)}%` }}>
                {String(t.h).padStart(2, '0')}
              </span>
            ))}
          {days.map((t) => (
            <span key={`d${t.i}`} className="day" style={{ left: `${pct(t.i)}%` }}>
              {dayLabel(stamps[t.i])}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
