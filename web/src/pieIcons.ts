/**
 * 発電所の「容量＝円の大きさ、実発電＝中の扇形」を描くためのアイコン生成。
 *
 * ## なぜアイコンなのか
 *
 * Mapbox の `circle` レイヤーは扇形を描けない。`symbol` レイヤーなら
 * canvas で作った任意の画像を使えるので、扇形を焼いたアイコンを
 * 「電源種 × 利用率」の組み合わせぶん用意して切り替える。
 *
 * ## 組み合わせ数
 *
 * 電源種 7 × 利用率 21 段（0〜100% を 5% 刻み）＝ 147 枚。
 * 64px の canvas なので生成は一瞬。段階を細かくしすぎると枚数が増えるだけで
 * 見た目は変わらないため 5% 刻みで足りる。
 *
 * ## ⚠️ icon-size の除数は ICON_PX ではなく DRAW_DIAMETER
 *
 * 描いている円は半径 `ICON_PX/2 - 4 = 28`、つまり**直径 56px** で、
 * 画像の 64px 全部は使っていない（外周の線幅ぶん余白を取っている）。
 * `icon-size = 直径 / 64` と書くと実際の円が 12.5% 小さくなり、
 * 同じ容量でも circle レイヤーの点より小さく見える。
 * 換算は `layers.ts` の capIconSize が DRAW_DIAMETER を使って行う。
 */
// ⛔ `slots.ts`（配列アクセス）は**使わない**。理由は下の iconId / pieIconExpr。

export const ICON_PX = 64
/** 利用率の段階数（0〜100% を CF_STEPS 段に量子化） */
export const CF_STEPS = 20
/** 実際に描かれる円の直径（px）。icon-size の換算に使う */
export const DRAW_DIAMETER = ICON_PX - 8

/** 電源種 → 色。layers.ts の FUEL_COLOR と揃えること */
export const FUEL_COLORS: Record<string, string> = {
  nuclear: '#b06ff5',
  // ★ 火力は暖色で明度の段階（炭素強度の順）。4 つ目は暖色に入らないため中性色
  gas: '#ffd54a',
  oil: '#f57c1f',
  coal: '#d13b30',
  other_thermal: '#7e8a97',
  hydro: '#35c4d4',
  geothermal: '#f472b6',
  other: '#9aa5b1',
}

/**
 * 段階を表す 1 文字。publish 側の `FAN_CHARS` と**同じ並び**にすること。
 * 0 段 = 'A' … 20 段 = 'U'、欠測 = '-'。
 */
export const FAN_CHARS = 'ABCDEFGHIJKLMNOPQRSTU'
export const FAN_MISSING = '-'

/** アイコン ID。⚠️ publish の `fsteps` が返す文字をそのまま末尾に使う */
export function iconId(fuel: string, step: number): string {
  return `pie-${fuel}-${FAN_CHARS[step]}`
}

/**
 * コマ i の扇形アイコンを選ぶ式（`icon-image` 用）。
 *
 * ⚠️ 扇形が表すのは**その時刻の出力 ÷ 容量**であって当日平均ではない。
 *    実測：原町は実時刻 94% に対し当日平均 48%、船町は 0% に対し 39%。
 *    平均で描くと、止まっている機組が動いているように見える。
 *
 * ⚠️ id の組み立ては `iconId()` と同じ規則にすること。ずれると存在しない
 *    画像を指してアイコンが**全部消える**。
 */
export function pieIconExpr(i: number): unknown[] {
  return [
    'concat',
    'pie-',
    FUEL_EXPR,
    '-',
    // ★ publish が焼いた 1 文字を取り出すだけ。計算しない
    ['slice', ['get', 'fsteps'], i, i + 1],
  ]
}

/** 電源種 → アイコン名の一部。式を 2 か所に書かないため切り出す */
const FUEL_EXPR: unknown[] = [
  'match', ['coalesce', ['get', 'fuel'], ''],
  'nuclear', 'nuclear', 'coal', 'coal', 'gas', 'gas',
  'oil', 'oil', 'hydro', 'hydro', 'geothermal', 'geothermal',
  'other_thermal', 'other_thermal',
  'other',
]

/* ⚠️ 以前は欠測コマの機組を filter で**描画から外して**いたが、
   2026-08-07 の要求で「欠測でも円環は出す」に変更した。
   区別は破線（`drawPie` の cf === null）が担う。
   ⛔ 欠測を隠す filter を復活させないこと。 */

/**
 * @param cf 0〜1 の利用率。**null は「データが無い」**（要求 2026-08-07：
 *   欠測でも円環は出す）。
 *
 * ⚠️ 欠測を「扇形ゼロ」と同じ絵にしてはいけない —— 止まっている機組と
 *    区別がつかなくなる。欠測は**破線の円環**にして、実線＝データあり、
 *    破線＝データ無し、と一目で分かるようにする。
 */
function drawPie(color: string, cf: number | null): ImageData {
  const c = document.createElement('canvas')
  c.width = c.height = ICON_PX
  const ctx = c.getContext('2d')!
  const cx = ICON_PX / 2
  const r = ICON_PX / 2 - 4

  // ① 外周＝設備容量。稼働ゼロでも「そこに発電所がある」ことは見える
  ctx.beginPath()
  ctx.arc(cx, cx, r, 0, Math.PI * 2)
  ctx.fillStyle = 'rgba(11,15,20,0.55)'          // 下の線が透けすぎないよう軽く敷く
  ctx.fill()
  ctx.lineWidth = 3
  ctx.strokeStyle = color
  if (cf === null) {
    // データ無し：**破線だけ**で区別する。⛔ 実線に戻さないこと（上の注記）
    // ⛔ 透明度は下げない（要求 2026-08-07）—— 薄くすると「弱く発電している」
    //    ように読めてしまう。データの有無と量は別の軸。
    ctx.setLineDash([5, 4])
    ctx.lineWidth = 2.5
  }
  ctx.stroke()
  ctx.setLineDash([])

  // ② 内側の扇形＝その時刻の出力÷容量。12 時方向から時計回り
  if (cf !== null && cf > 0) {
    ctx.beginPath()
    ctx.moveTo(cx, cx)
    ctx.arc(cx, cx, r - 2, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * cf)
    ctx.closePath()
    ctx.fillStyle = color
    ctx.fill()
  }
  return ctx.getImageData(0, 0, ICON_PX, ICON_PX)
}

/** 全組み合わせを map に登録する。既にあるものは飛ばす */
export function registerPieIcons(map: {
  hasImage: (id: string) => boolean
  addImage: (id: string, img: ImageData, opts?: { pixelRatio?: number }) => void
}) {
  for (const [fuel, color] of Object.entries(FUEL_COLORS)) {
    for (let i = 0; i <= CF_STEPS; i++) {
      const id = iconId(fuel, i)
      if (map.hasImage(id)) continue
      map.addImage(id, drawPie(color, i / CF_STEPS), { pixelRatio: 2 })
    }
    // 欠測用。`fsteps` が '-' を返すコマがこれを指す
    const miss = `pie-${fuel}-${FAN_MISSING}`
    if (!map.hasImage(miss)) map.addImage(miss, drawPie(color, null), { pixelRatio: 2 })
  }
}
