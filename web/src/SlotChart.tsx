/**
 * 時間軸の**真上**に貼る時系列グラフ。潮流なら潮流、機組なら出力。
 *
 * ## ★ 目盛りと横軸を共有する
 *
 * タイムバーの中（罫のすぐ上）に置き、x を **0〜100 の百分率**で描く。
 * 罫・日付・游標も同じ百分率で置いているので、**ピクセル単位で揃う**。
 * 別枠に出すと座標系が 2 つになり、「この山は何時か」を読めなくなる。
 *
 * `viewBox="0 0 100 H"` + `preserveAspectRatio="none"` で横に引き伸ばす。
 * ⚠️ そのままだと線の太さも歪むので、線には `vector-effect="non-scaling-stroke"`。
 *
 * ## ⚠️ 欠測は線を切る
 *
 * 0 として繋ぐと「出力が落ちた」ように見える。値が無いコマは**描かない**
 * （機組は T+1 公表なので、直近 1 日以上が普通に欠測する）。
 */
import { seriesStat } from './slotSeries'

type Props = {
  series: (number | null)[]
  /** 表示中のコマ */
  index: number
  /** 見出し（何のグラフか） */
  label: string
  /** 単位つきの値を作る */
  unit?: string
}

const H = 46          // 高さ（px）。罫と数字のぶんを足しても timebar が太らない範囲
const PAD_T = 5       // 上端の余白。最大値が枠に触らないように
const PAD_B = 3

export function SlotChart({ series, index, label, unit = 'MW' }: Props) {
  const n = series.length
  if (n < 2) return null
  const { max, signed } = seriesStat(series)

  const x = (i: number) => (i / (n - 1)) * 100
  // 符号があるものは 0 を中央に、無いものは 0 を下端に置く
  const zeroY = signed ? PAD_T + (H - PAD_T - PAD_B) / 2 : H - PAD_B
  const span = signed ? (H - PAD_T - PAD_B) / 2 : H - PAD_T - PAD_B
  const y = (v: number) => zeroY - (v / max) * span

  // 欠測でパスを切る。連続している区間ごとに、線と塗りの 2 本を作る。
  // ⚠️ 塗りは区間ごとに閉じること。全部まとめて閉じると
  //    欠測をまたいで面が繋がり、無いデータがあるように見える。
  const runs: number[][] = []
  let run: number[] = []
  for (let i = 0; i < n; i++) {
    if (series[i] === null) {
      if (run.length) runs.push(run)
      run = []
    } else {
      run.push(i)
    }
  }
  if (run.length) runs.push(run)

  const pt = (i: number) => `${x(i).toFixed(3)} ${y(series[i] as number).toFixed(2)}`
  const d = runs
    .map((r) => 'M' + r.map(pt).join('L'))
    .join(' ')
  // 面は 0 線まで下ろして閉じる。1 点しか無い区間は面にならないので除く
  const dArea = runs
    .filter((r) => r.length > 1)
    .map((r) => {
      const first = r[0]
      const last = r[r.length - 1]
      return `M${x(first).toFixed(3)} ${zeroY.toFixed(2)}`
        + 'L' + r.map(pt).join('L')
        + `L${x(last).toFixed(3)} ${zeroY.toFixed(2)}Z`
    })
    .join(' ')

  const cur = series[index]
  const fmt = (v: number) =>
    `${Math.abs(v) >= 10 ? Math.round(v).toLocaleString('ja-JP') : v.toFixed(1)} ${unit}`

  return (
    <div className="slotchart">
      <div className="slotchart-head">
        <span className="slotchart-label">{label}</span>
        <span className="slotchart-val">
          {cur === null || cur === undefined ? '欠測' : fmt(cur)}
        </span>
      </div>
      <svg
        className="slotchart-svg"
        viewBox={`0 0 100 ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${label}の時系列`}
      >
        {/* ⚠️ グラデーションの id は**この図で一意**にする。
            同じ id が 2 つあると片方が他方の定義を拾う */}
        <defs>
          <linearGradient id="slotchart-fade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--text-dim)" stopOpacity="0.34" />
            <stop offset="100%" stopColor="var(--text-dim)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* 0 の線。符号があるときだけ意味があるので、無いときは下端＝枠と重なる */}
        <line className="zero" x1="0" x2="100" y1={zeroY} y2={zeroY}
              vectorEffect="non-scaling-stroke" />
        {dArea && <path className="area" d={dArea} />}
        {d && <path className="line" d={d} vectorEffect="non-scaling-stroke" />}
        {/* 表示中のコマ。タイムバーの游標と同じ x に立つ */}
        <line className="cursor" x1={x(index)} x2={x(index)} y1="0" y2={H}
              vectorEffect="non-scaling-stroke" />
      </svg>
      {/* 値域は上端だけ出す。軸を引くとグラフより目盛りの方が目立つ */}
      <span className="slotchart-max">
        {signed ? `±${Math.round(max).toLocaleString('ja-JP')}` : Math.round(max).toLocaleString('ja-JP')} {unit}
      </span>
    </div>
  )
}
