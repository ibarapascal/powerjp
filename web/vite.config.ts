import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * データは dist に含めない。
 *
 * 本番では GeoJSON は R2 → CDN に置き、`VITE_DATA_BASE` で指す。
 * dev のときだけ ../storage/publish/ を /data/ として配信する（下のプラグイン）。
 * public/ に置くと build 時に 10 MB が dist へコピーされてしまうので使わない。
 */
const PUBLISH_DIR = resolve(__dirname, '../storage/publish')

function serveData() {
  return {
    name: 'powerjp-serve-data',
    configureServer(server: any) {
      server.middlewares.use('/data', (req: any, res: any, next: any) => {
        const name = decodeURIComponent((req.url || '').split('?')[0]).replace(/^\//, '')
        // パストラバーサル防止：ファイル名のみ許可
        if (!name || name.includes('/') || name.includes('..')) return next()
        const file = resolve(PUBLISH_DIR, name)
        if (!existsSync(file) || !statSync(file).isFile()) return next()
        res.setHeader('Content-Type', 'application/geo+json; charset=utf-8')
        res.setHeader('Cache-Control', 'no-cache')
        createReadStream(file).pipe(res)
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), serveData()],
  server: { host: '0.0.0.0', port: 8081 },
  build: { outDir: 'dist', assetsInlineLimit: 0 },
})
