import http from 'node:http'

import { BodyTooLargeError, InvalidJsonError, isRecord, notFound, sendJson } from './http.ts'

// params のキーはパターン中の :name。値は ([^/]+) にコンパイルされるので、そのルートに
// 到達した時点で必ず1文字以上ある（型の上でだけ undefined がありうる）。
type RouteContext = { params: Record<string, string>; query: URLSearchParams }
type RouteHandler = (
  req: http.IncomingMessage,
  res: http.ServerResponse,
  ctx: RouteContext,
) => void | Promise<void>
export type AddRoute = (method: string, pattern: string, handler: RouteHandler) => void

export function createRouter() {
  const routes: {
    method: string
    regex: RegExp
    paramNames: string[]
    handler: RouteHandler
  }[] = []

  const addRoute: AddRoute = (method, pattern, handler) => {
    const paramNames: string[] = []
    const regexStr =
      '^' +
      pattern.replace(/:[a-zA-Z_]+/g, (m) => {
        paramNames.push(m.slice(1))
        return '([^/]+)'
      }) +
      '$'
    routes.push({ method, regex: new RegExp(regexStr), paramNames, handler })
  }

  // 一致するルートを実行する。どれにも一致しなければ 404
  async function dispatch(
    req: http.IncomingMessage,
    res: http.ServerResponse,
    pathname: string,
    query: URLSearchParams,
  ) {
    for (const route of routes) {
      if (route.method !== req.method) continue
      const m = pathname.match(route.regex)
      if (!m) continue
      const params: Record<string, string> = {}
      route.paramNames.forEach((name, i) => {
        // paramNames はパターン中の :name を ([^/]+) に置き換えた順に積んであるので、
        // マッチした m にはその数だけキャプチャ群が必ずある。
        params[name] = m[i + 1]!
      })
      try {
        await route.handler(req, res, { params, query })
      } catch (err) {
        // 応答を書き始めた後なら、もう伝えようがない
        if (res.headersSent) {
          res.destroy()
          return
        }
        if (err instanceof InvalidJsonError) {
          return sendJson(res, 400, { error: 'invalid_json', message: err.message })
        }
        if (err instanceof BodyTooLargeError) {
          // 読み残しは貯めずに読み捨てる。接続を切ると、送信中のクライアントには 413 より
          // 先に EPIPE が届き、断られた理由が伝わらない
          req.resume()
          return sendJson(res, 413, { error: 'body_too_large', message: err.message })
        }
        // err は unknown。message を持つ値ならそれを使い、無ければ値そのものを文字にする
        // （fetch の失敗のように Error 以外が飛ぶ経路もある）。
        const message = isRecord(err) && err.message ? err.message : err
        sendJson(res, 500, { error: 'internal_error', message: String(message) })
      }
      return
    }
    notFound(res)
  }

  return { addRoute, dispatch }
}
