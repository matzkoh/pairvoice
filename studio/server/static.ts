import fsp from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'

import { notFound } from './http.ts'
import { DIST_DIR } from './paths.ts'

const STATIC_CONTENT_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
}

// 開発中に :17494 を直接開くと必ずこの経路を通る。無言で壊れると
// 「読み込み中…で固まる」のと同じ迷い方をするので、次にやることを書く。
const DIST_MISSING_HTML = `<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>pairvoice studio</title></head>
<body style="font-family: system-ui; padding: 2rem; line-height: 1.7">
<h1>ビルド成果物がありません</h1>
<p>開発中は <code>pnpm dev</code> を起動して
<a href="http://127.0.0.1:17493">http://127.0.0.1:17493</a> を開いてください。</p>
<p>このポートで見たい場合は <code>pnpm build</code> を実行してください。</p>
</body></html>
`

export async function serveStatic(pathname: string, res: http.ServerResponse) {
  const base = path.resolve(DIST_DIR)
  const rel = pathname === '/' ? '/index.html' : pathname
  const resolved = path.resolve(base, '.' + rel)
  if (resolved !== base && !resolved.startsWith(base + path.sep)) return notFound(res)

  // SPA なので、実ファイルに対応しないパス（/review など）も index.html を返す。
  // ここで初めて Router がクライアント側でルーティングする。
  let servedPath = resolved
  let content: Buffer
  try {
    content = await fsp.readFile(resolved)
  } catch {
    try {
      servedPath = path.join(base, 'index.html')
      content = await fsp.readFile(servedPath)
    } catch {
      res.writeHead(503, { 'Content-Type': 'text/html; charset=utf-8' })
      res.end(DIST_MISSING_HTML)
      return
    }
  }
  const ext = path.extname(servedPath)
  res.writeHead(200, { 'Content-Type': STATIC_CONTENT_TYPES[ext] || 'application/octet-stream' })
  res.end(content)
}
