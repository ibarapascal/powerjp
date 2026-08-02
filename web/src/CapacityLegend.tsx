/**
 * 「円の大きさ＝設備容量」を示す同心円の凡例。
 *
 * 底辺を揃えた入れ子の円で、対数的なスケールを直感的に見せる定番の形。
 * ⚠️ 半径は `layers.ts` の `capRadiusPx()` から取る。地図と同じ関数を使うこと
 *    —— 数値を書き写すと、スケールを調整したときに凡例だけ古くなる。
 */
import { capRadiusPx } from './layers'

/**
 * 目盛に出す容量。
 * ⚠️ 6,000MW は入れない —— 円が大きくなりすぎてサイドバーを圧迫する割に、
 *    該当する発電所がごく少数で読み取りの助けにならない（2026-08-02）。
 */
const TICKS_MW = [10, 100, 1000]

const PAD = 4
const LABEL_GAP = 8

/** 半径 → 輪郭の太さ。地図の capStrokeWidth と同じ考え方（半径比 一定） */
const strokeFor = (r: number) => Math.max(0.6, Math.min(3, 0.6 + (r / 34) * 2.4))

export function CapacityLegend() {
  const radii = TICKS_MW.map((mw) => capRadiusPx(mw, 11))
  const rMax = Math.max(...radii)
  const w = rMax * 2 + 92
  const h = rMax * 2 + PAD * 2
  const cx = rMax + PAD
  const baseY = h - PAD          // 底辺を揃える

  return (
    <div className="cap-legend">
      <p className="cap-legend-title">円の大きさ ＝ 設備容量</p>
      <svg width={w} height={h} role="img" aria-label="設備容量と円の大きさの対応">
        {/* 大きい順に描いて、小さい円が上に来るようにする */}
        {radii
          .map((r, i) => ({ r, mw: TICKS_MW[i] }))
          .sort((a, b) => b.r - a.r)
          .map(({ r, mw }) => {
            const top = baseY - r * 2
            return (
              <g key={mw}>
                {/* 輪郭の太さも地図と同じく半径に連動させる */}
                <circle cx={cx} cy={baseY - r} r={r} strokeWidth={strokeFor(r)} />
                <line x1={cx} y1={top} x2={rMax * 2 + PAD + LABEL_GAP} y2={top} />
                <text x={rMax * 2 + PAD + LABEL_GAP + 4} y={top + 3}>
                  {mw.toLocaleString('ja-JP')} MW
                </text>
              </g>
            )
          })}
      </svg>
      <p className="cap-legend-note">
        0.4 乗スケール。面積比例だと大型が画面を覆い、対数だと小型が大きすぎるため
      </p>
    </div>
  )
}
