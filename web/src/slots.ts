/**
 * `slots_mw`（30 分コマの時系列）を Mapbox の式から安全に引くための唯一の出処。
 *
 * 潮流（`flowGlow.ts`）と機組の扇形（`pieIcons.ts`）が同じ罠を踏むので、
 * **式の組み立てはここに集約する**。片方だけ直して片方を忘れると、
 * 症状（色が出ない／扇形が消える）が別物に見えて原因に辿り着けない。
 *
 * ## ⛔ `['at', i, ['get', 'slots_mw']]` と書いてはいけない（LESSONS #42）
 *
 * `at` の結果を数値として使うと、Mapbox は配列の引数に
 * `["array","number",…]` の型アサーションを**暗黙に挿入する**。
 * `slots_mw` は欠測コマが null なので実行時の型は `array<value>` になり、
 * アサーションが落ちて**式全体が評価不能**になる：
 *
 *     The expression ["get","slots_mw"] evaluated to array<value, 144>
 *     but was expected to be of type array<number>
 *
 * 失敗した式は既定値に落ちる —— `line-color` は黒、`icon-image` は無し。
 * つまり**流光が黒くなり、扇形が消える**。console には error ではなく
 * **warning** として出るので、error だけ見ていると気づけない。
 *
 * ## ⚠️ アサーションは配列**全体**にかかる
 *
 * null が 1 コマでも混じれば、`i` が何であろうと全コマ死ぬ。
 * 「最新コマだけ欠測」なのに「どのコマでも出ない」という症状になり、
 * 原因から遠い所に症状が出る。実際これで丸一回分の調査を空振りさせた。
 *
 * ## 正しい書き方
 *
 * `['array', expr]`（**型名を書かない**形）で「要素型 value の配列」と
 * 明示すれば、暗黙のアサーションは入らない。
 * ⚠️ `['array','value',…]` は**不正** —— 型名に取れるのは string/number/boolean だけ。
 */

/** コマ i の生値。型は `value`（数値 or null）。欠測を区別したい時に使う */
export const slotRawAt = (i: number): unknown[] => [
  'at', i, ['array', ['get', 'slots_mw']],
]

/** コマ i の値を数値として。**欠測は 0** に倒す */
export const slotNumAt = (i: number): unknown[] => [
  'case', ['==', slotRawAt(i), null], 0, ['to-number', slotRawAt(i)],
]

/** コマ i が欠測かどうか。要素を「描かない」判定に使う */
export const slotMissingAt = (i: number): unknown[] => [
  '==', slotRawAt(i), null,
]
