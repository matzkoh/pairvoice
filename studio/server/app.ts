import http from 'node:http'

import { notFound, sendJson } from './http.ts'
import { createRouter } from './router.ts'
import { registerPairvoiceRoutes } from './routes/pairvoice.ts'
import { serveStatic } from './static.ts'

// 127.0.0.1 にだけ待ち受けていても、ブラウザで開いた外部のページから2つの経路で届く。
// - DNS rebinding: 外部の名前を 127.0.0.1 に解決させる。Host が外部の名前のままなので Host で断る
// - 単純な POST（no-cors）: Host は 127.0.0.1 のまま届くが、ブラウザが Origin に外部のページを付ける
// どちらも、ループバックの名前でなければ断る（ポートは問わない。dev server の :17493 から来るため）
const LOCAL_HOSTNAMES = new Set(['127.0.0.1', 'localhost', '[::1]'])

function isLocalHost(host: string | undefined) {
  if (!host) return false
  // 末尾の :port を落とす。IPv6 は [::1]:port なので、角括弧の外のコロンだけを見る
  return LOCAL_HOSTNAMES.has(host.replace(/:\d*$/, '').toLowerCase())
}

// Origin はブラウザしか付けない（curl やフック、テストには無い）ので、無ければ通す。
// サンドボックスの iframe などは "null" を送ってくるので、それも断る
function isLocalOrigin(origin: string | undefined) {
  if (origin === undefined) return true
  return URL.canParse(origin) && isLocalHost(new URL(origin).host)
}

export function createStudioServer() {
  const router = createRouter()
  registerPairvoiceRoutes(router.addRoute)

  async function handle(req: http.IncomingMessage, res: http.ServerResponse) {
    if (!isLocalHost(req.headers.host) || !isLocalOrigin(req.headers.origin)) {
      return sendJson(res, 403, { error: 'forbidden_origin' })
    }
    let url: URL
    let pathname: string
    try {
      // http.Server が渡すリクエストには url が必ず入る（型の上でだけ optional）
      url = new URL(req.url!, 'http://127.0.0.1')
      pathname = decodeURIComponent(url.pathname)
    } catch {
      // 絶対形式の壊れた URL や、壊れたパーセントエンコード
      return sendJson(res, 400, { error: 'bad_path' })
    }

    if (!pathname.startsWith('/api/')) {
      if (req.method === 'GET') return serveStatic(pathname, res)
      return notFound(res)
    }
    await router.dispatch(req, res, pathname, url.searchParams)
  }

  // 投げられたまま返すと未処理の reject になり、1件の要求でプロセスごと落ちる
  return http.createServer((req, res) => {
    handle(req, res).catch((err: unknown) => {
      if (res.headersSent) res.destroy()
      else sendJson(res, 500, { error: 'internal_error', message: String(err) })
    })
  })
}
