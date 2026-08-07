/**
 * `slots_mw`（30 分コマの時系列）を **JS 側で**読むためのもの。
 *
 * ⚠️ 式（Mapbox）から引く場合は `slots.ts` を使う。あちらとは別物。
 *    こちらは情報パネルとグラフ用 —— JS では配列が**文字列で**渡ってくる
 *    ことがあるので、必ずここを通して読む（LESSONS #41）。
 */

/** Mapbox が返す `slots_mw` を配列に戻す。読めなければ null */
export function parseSlots(v: unknown): (number | null)[] | null {
  if (Array.isArray(v)) return v as (number | null)[]
  if (typeof v !== 'string') return null
  try {
    const p = JSON.parse(v)
    return Array.isArray(p) ? p : null
  } catch {
    return null
  }
}

export type SeriesStat = {
  /** 値域の上端（絶対値）。0 のときは 1 に倒して 0 除算を避ける */
  max: number
  /** 負の値があるか＝潮流の向きが反転する線かどうか */
  signed: boolean
}

export function seriesStat(series: (number | null)[]): SeriesStat {
  let max = 0
  let signed = false
  for (const v of series) {
    if (v === null) continue
    if (v < 0) signed = true
    const a = Math.abs(v)
    if (a > max) max = a
  }
  return { max: max || 1, signed }
}
