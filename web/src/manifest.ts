/**
 * `manifest.json` の読み取りと監視。
 *
 * ## これが要る理由
 *
 * publish は成果物を 2 層に分けている（`pipeline/publish/spec.py` が唯一の出所）：
 *
 *     static   送電線・変電所・推算サイト   AJG が新版を出したときだけ変わる（19.6MB）
 *     dynamic  潮流・機組・コマ時刻         **30 分ごとに変わる**（約 2.3MB）
 *
 * 前端がこの区別を持たないと、
 * - 一度読んだら二度と更新しない（＝「準リアルタイム」を名乗れない）
 * - あるいは全部を取り直す（＝ 30 分ごとに 22MB）
 * のどちらかになる。manifest の `mtime` を見て、**変わった層だけ**取り直す。
 *
 * ## ⚠️ これは「原子性」の保証ではない
 *
 * `CLAUDE.md` 約定 8 は「産物を全部 R2 に上げてから最後に manifest を更新するので、
 * 前端は半端な組み合わせを読まない」と書いているが、それが成立するのは
 * **ファイル名に内容ハッシュが入っている場合**だけ。今の配信は同じ名前を
 * 上書きするので、manifest を読んでも「読んだ瞬間に中身が入れ替わる」ことは防げない。
 * 本当の原子性はアップロード側（`DEPLOY.local.md` B1）の仕事。
 * ここでやるのは**鮮度**の管理と、ずれたときに気づくこと。
 */
import { useEffect, useState } from 'react'

export type ManifestFile = {
  /** stable logical name used by layers.ts */
  file: string
  /** production object name; absent in local/dev publish output */
  object?: string
  bytes: number
  mtime: number
  sha256?: string
}
export type Manifest = {
  built?: string[]
  static?: ManifestFile[]
  dynamic?: ManifestFile[]
}

export const dataBase = () =>
  ((import.meta.env.VITE_DATA_BASE as string | undefined) ?? '/data')
    .replace(/\/$/, '')

/** logical publish filename → local filename or immutable CDN object name */
export function dataFile(filename: string, m: Manifest | null): string {
  for (const f of [...(m?.static ?? []), ...(m?.dynamic ?? [])]) {
    if (f.file === filename) return f.object ?? f.file
  }
  return filename
}

export function dataUrl(filename: string, m: Manifest | null): string {
  return `${dataBase()}/${dataFile(filename, m)}`
}

/**
 * ファイル名 → 更新時刻。**static も dynamic も一緒に見る。**
 *
 * ⚠️ 以前は dynamic の最大 mtime だけを見ていたが、それだと
 *    static（AJG の新版、配信を保留した層の変更など）が更新されても
 *    前端が永久に気づかない。実際、水力を配信から外したときに
 *    画面が古いままになった。**層で区別せず、ファイルごとに比べる。**
 */
export function fileStamps(m: Manifest | null): Record<string, number> {
  const out: Record<string, number> = {}
  for (const f of [...(m?.static ?? []), ...(m?.dynamic ?? [])]) {
    out[f.file] = f.mtime || 0
  }
  return out
}

/** 全ファイルの更新時刻の合計。1 つでも変われば値が変わる */
function allStamp(m: Manifest | null): number {
  return Object.values(fileStamps(m)).reduce((a, b) => a + b, 0)
}

/**
 * manifest を定期的に読み、dynamic 層が更新されたら `version` を進める。
 *
 * ⚠️ ポーリング間隔は**データの更新間隔（30 分）より短く、かつ十分に粗く**。
 *    manifest は 600 バイト程度なので 5 分でも負荷は無視できるが、
 *    1 分にしても新しくなるわけではない（元が 30 分更新）。
 */
export function useManifest(pollMs = 5 * 60_000) {
  const [manifest, setManifest] = useState<Manifest | null>(null)
  /** どれか 1 つでも更新されたら増える。データ再取得の引き金 */
  const [version, setVersion] = useState(0)

  useEffect(() => {
    let alive = true
    let last = -1

    const read = async () => {
      try {
        const res = await fetch(`${dataBase()}/manifest.json`, { cache: 'no-cache' })
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const m: Manifest = await res.json()
        if (!alive) return
        setManifest(m)
        const s = allStamp(m)
        // 初回は version を進めない（初期ロードが二重に走らないように）
        if (last >= 0 && s !== last) setVersion((v) => v + 1)
        last = s
      } catch (err) {
        // ⚠️ 落としてはいけない。manifest が無くても地図は出るべき
        //    （dev で publish していない、配信が一時的に落ちている等）
        console.warn('[powerjp] manifest.json を読めなかった:', err)
      }
    }

    read()
    const t = setInterval(read, pollMs)
    return () => { alive = false; clearInterval(t) }
  }, [pollMs])

  return { manifest, version }
}

