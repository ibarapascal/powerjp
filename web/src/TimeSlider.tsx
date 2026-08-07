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
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

type Props = {
  stamps: string[]
  index: number
  /** 機組の実測が入っている最後のコマ。これより後ろは調整力が出ない */
  unitLastIdx: number
  /** 罫のすぐ上に貼る時系列グラフ。**横軸を共有する**ので中に入れる */
  chart?: ReactNode
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
 *
 * ⚠️ 曜日は**別要素**にして付ける（`.wd`）。理由は下の「中央揃え」の注記 ——
 *    曜日まで含めて中央に置くと、日付が 0 時の罫からずれる。
 */
function dayLabel(stamp: string): string {
  const d = stamp.split(' ')[0]
  return `${Number(d.slice(4, 6))}/${Number(d.slice(6, 8))}`
}

const hourOf = (s: string) => Number(s.split(' ')[1]?.slice(0, 2) ?? -1)
const isOnTheHour = (s: string) => s.endsWith(':00')

/** `8/7 Fri 13:22`。左上の実測時刻と同じ並びにして見比べられるようにする */
function nowLabel(): string {
  const d = new Date()
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${WDAY[d.getDay()]} ${hh}:${mm}`
}

export function TimeSlider({ stamps, index, unitLastIdx, chart, onChange }: Props) {
  // 閉じるときも中身を少し残す。即 unmount すると高さだけ縮んで
  // 中身が瞬間的に消え、畳まれた感じにならない
  const [held, setHeld] = useState(chart)
  useEffect(() => {
    if (chart) { setHeld(chart); return }
    const t = setTimeout(() => setHeld(null), 220)
    return () => clearTimeout(t)
  }, [chart])

  const [now, setNow] = useState(nowLabel)
  // ⚠️ **一定周期の setInterval にしない。** 表示は「分」なのに周期が分の
  //    境目と無関係だと、最悪その周期ぶん遅れて切り替わる（20 秒周期なら
  //    最大 20 秒遅れ）。**次の分の頭まで待つ**方式にする。
  //    毎回そこから測り直すので、ズレが溜まらない（タブが眠って
  //    起きた直後でも 1 分以内に正しい値へ戻る）。
  useEffect(() => {
    let id = 0
    const schedule = () => {
      // 分の境目 +50ms に起きる。50ms は境目の手前で起きて
      // 1 分前の値を表示してしまうのを避けるための余裕
      id = window.setTimeout(() => {
        setNow(nowLabel())
        schedule()
      }, 60_000 - (Date.now() % 60_000) + 50)
    }
    schedule()
    return () => clearTimeout(id)
  }, [])
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

  // ★ 数字を何時間ごとに出すか。**窓の長さから決める**。
  //   幅 620px で数字は約 11px。間隔が 20px を切ると隣とぶつかるので、
  //   72h までは 3 時間ごと、それより長ければ 6 時間ごと。
  //   ⚠️ 罫は 3 時間ぶんを残す —— 数字が減っても目盛りの細かさは失われない。
  const labelEvery = stamps.length > 96 * 2 ? 6 : 3

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
        {/* ★ ブラウザの現在時刻。**データの末尾との差＝どれだけ古いか**が
            一目で分かる。パイプラインが止まっていても画面上は
            「最新コマ」に見えてしまうので、比較対象を常に出しておく。 */}
        <span className="timebar-range">{now}</span>
      </div>

      {/* ★ グラフは罫の真上。x は同じ百分率なので目盛りと揃う。
          ⚠️ 出入りは高さのアニメーション。要素を即座に消すと
          タイムバーの高さがガクッと変わって、下の地図まで揺れて見える。 */}
      <div className={`slotchart-wrap ${chart ? 'open' : ''}`}>
        <div className="slotchart-inner">{held}</div>
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
            .filter((t) => t.kind === 'day' || t.h % labelEvery === 0)
            .map((t) => (
              <span key={t.i} className="hour" style={{ left: `${pct(t.i)}%` }}>
                {String(t.h).padStart(2, '0')}
              </span>
            ))}
          {days.map((t) => (
            <span key={`d${t.i}`} className="day" style={{ left: `${pct(t.i)}%` }}>
              {dayLabel(stamps[t.i])}
              {/* ⚠️ 曜日は**絶対配置で外に出す**（要求 2026-08-07）。
                  親の幅に含めないので、中央揃えの基準は日付だけになり
                  `8/5` が 0 時の罫の真上に来る。文字数が変わる曜日
                  （Tue と Wednesday の略でも幅が違う）を勘定に入れると
                  日ごとに位置がずれる。 */}
              <i className="wd">{wday(stamps[t.i].split(' ')[0])}</i>
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
