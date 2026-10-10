import path from 'node:path'

import babel from '@rolldown/plugin-babel'
import tailwindcss from '@tailwindcss/vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 画面と API はどちらも pairvoice（17495）が配る。dev server は 17493 を所有する。
// strictPort を立てるのは、衝突時に黙って次の空きポートへ移られると URL が
// 変わるため（Vite の既定は strictPort:false）。タブを開いたままにする道具では
// 番号が動かないことのほうが大事なので、衝突は黙って回避せずエラーにする。
const DEV_PORT = 17493
// host を書かないと Vite の既定 'localhost' が macOS では ::1 だけに bind し、
// 127.0.0.1 からは接続できない。pairvoice は 127.0.0.1 で待つ道具なので、
// 番号だけでなく待ち受けるアドレスもそちらに揃える。
const DEV_HOST = '127.0.0.1'
const PAIRVOICE_ORIGIN = 'http://127.0.0.1:17495'

// dev と preview に同じ proxy を渡す。片方だけ直すと、ビルド確認のときだけ
// 挙動が違うという厄介な差が生まれる
const proxy = { '/api': { target: PAIRVOICE_ORIGIN, changeOrigin: true } }

export default defineConfig({
  // root の既定は設定ファイルの場所ではなく process.cwd()。scripts は studio/ から
  // `vite --config web/vite.config.ts` を呼ぶので、明示しないと root が studio/ になり
  // index.html を studio/ に探しにいき、outDir も studio/dist に解決される。
  root: import.meta.dirname,
  plugins: [react(), babel({ presets: [reactCompilerPreset()] }), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  server: { host: DEV_HOST, port: DEV_PORT, strictPort: true, proxy },
  preview: { host: DEV_HOST, port: DEV_PORT, strictPort: true, proxy },
  // どちらも Vite の既定と同値だが、pairvoice（studio_web.py）が web/dist を静的配信する
  // 契約なので明示しておく（既定に頼ると出力先が動いたときに気づけない）。
  build: { outDir: 'dist', emptyOutDir: true },
})
