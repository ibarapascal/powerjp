/**
 * 出典とライセンスの**唯一の出処**（前端側）。
 *
 * ⚠️ repo 側の `ATTRIBUTION.md` と**必ず一致させる**こと。あちらが正本で、
 *    ここはその表示用の写し。片方だけ直すと、画面に出ている表記と
 *    実際の取り決めがずれる —— それは表示の不備ではなく**規約違反**になりうる。
 *
 * ⚠️ `status` は画面に出さない。`ATTRIBUTION.md` の規則 4 が
 *    「`TO VERIFY` は公開を止める」なので、公開時点で `unverified` が
 *    残っていること自体がおかしい —— 表示するより、残っていたら
 *    そもそも配信しないのが筋。記録として持つだけにする。
 */

export type LicenseStatus = 'ok' | 'unverified'

export type LicenseEntry = {
  /** 出典名 */
  name: string
  /** 提供者 */
  provider: string
  /** この地図で何に使っているか */
  used: string
  /** ライセンス名・条件 */
  licence: string
  /** 再配布の可否と条件 */
  redistribution: string
  url: string
  /**
   * 許諾の確認状態。⚠️ **画面には出さない**（2026-08-07 に表示を削除）。
   * 公開前チェックの記録として残しているだけ。正本は `ATTRIBUTION.md`。
   */
  status: LicenseStatus
  /** 注意書き（あれば） */
  note?: string
}

/**
 * ⚠️ 公開している成果物に**実際に入っている**源だけを載せる。
 * ⛔ 「今後こうする」という予定は書かない。**いま何であるか**だけを書く
 *    （予定は repo 外の私有メモ）。表示は現状の記述であって計画書ではない。
 *
 * ⚠️ 2026-08-07：JEPOC 水力発電所データベースをこの一覧から外した。
 *    許諾が未確認のため、由来する水力 518 件を配信物から除いたので
 *    （`pipeline/publish/geojson.py` の `SITES_WITHHELD_KINDS`）、
 *    **配信していないものをライセンス表示に載せない**。
 *    許諾が取れて配信を再開するときは、ここにも戻すこと。
 */
export const LICENSES: LicenseEntry[] = [
  {
    name: 'All-Japan-Grid（AJG）',
    provider: 'lutelute — OpenStreetMap 由来',
    used: '送電線・変電所の幾何、発電所の座標、潮流の経路探索に使う拓撲図',
    licence: 'データ ODbL / コード MIT',
    redistribution:
      '可。ただし出典表示に加え、**派生データベースも ODbL で公開**する義務がある（share-alike）',
    url: 'https://github.com/lutelute/All-Japan-Grid',
    status: 'ok',
    note: 'この地図が配信する GeoJSON は AJG の派生物なので、ODbL の継承対象。',
  },
  {
    name: 'OpenStreetMap',
    provider: 'OSM contributors',
    used: '上記 AJG の上流',
    licence: 'ODbL',
    redistribution: '可。出典表示 + share-alike',
    url: 'https://www.openstreetmap.org/copyright',
    status: 'ok',
  },
  {
    name: '系統情報サービス（地内基幹送電線 潮流実績）',
    provider: 'OCCTO（電力広域的運営推進機関）',
    used: '基幹送電線の 30 分ごとの潮流実績',
    licence:
      '「自由に利用できますが、その際は出典を記載してください。編集・加工して利用する場合は、出典とは別に編集・加工を行ったことを記載してください」',
    redistribution: '可（出典 + 加工した旨の明記が条件）',
    url: 'https://occtonet3.occto.or.jp/public/dfw/RP11/OCCTO/SD/LOGIN_login',
    status: 'ok',
    note: 'この地図の潮流は経路をグラフ上で求解した**加工物**。加工の明示が必要。',
  },
  {
    name: 'ユニット別発電実績公開システム',
    provider: 'OCCTO',
    used: '調整力機組の 30 分ごとの出力実績',
    licence: '上記と同じ OCCTO 利用条件',
    redistribution: '可（出典 + 加工した旨の明記）',
    url: 'https://hatsuden-kokai.occto.or.jp/hks-web-public/',
    status: 'ok',
    note: 'ユニットを発電所の座標に対応付けて表示している**加工物**。',
  },
  {
    name: '国土数値情報 発電施設（P03）',
    provider: '国土交通省',
    used: '発電所の座標補完',
    licence: '国土数値情報 利用約款（政府標準利用規約に準拠）',
    redistribution: '可（出典表示）',
    url: 'https://nlftp.mlit.go.jp/ksj/',
    status: 'ok',
    note: '「国土数値情報（発電施設データ）」（国土交通省）を加工して作成。',
  },
  {
    name: 'Global Integrated Power Tracker',
    provider: 'Global Energy Monitor',
    used: '発電所の座標・設備容量・電源種の補完',
    licence: 'Creative Commons Attribution 4.0 International（CC BY 4.0）',
    redistribution: '可。出典表示が条件で、派生物の作成・再配布も認められている',
    url: 'https://globalenergymonitor.org/creative-commons-license',
    status: 'ok',
    note: '引用は "Global Integrated Power Tracker, Global Energy Monitor, March 2026 release"。',
  },
  {
    name: 'エリア需給実績',
    provider: '一般送配電事業者 10 社',
    used: '非調整電源（風力・地熱・バイオマス）の按分推計の元データ',
    licence: '各社が個別に定める利用規約による（10 社で統一されていない）',
    redistribution:
      'この地図が配信しているのは各社の CSV そのものではなく、'
      + 'エリア合計を設備容量比で按分した推計値。'
      + '九州電力送配電の規約は、個人利用を除く無断の複製・引用・転載を禁じている。',
    url: 'https://www.occto.or.jp/keitoujouhou/index.html',
    status: 'unverified',
  },
]

/** 地図そのものの底図 */
export const BASEMAP_NOTE =
  '底図は Mapbox（© Mapbox, © OpenStreetMap contributors）。'
