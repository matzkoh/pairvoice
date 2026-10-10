import type http from 'node:http'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'

import type { PairvoiceHealth, StudioHealth } from '../../shared/api-types.ts'
import { badRequest, isRecord, readJsonBody, relayPairvoice, sendJson } from '../http.ts'
import { pairvoiceBase } from '../paths.ts'
import type { AddRoute } from '../router.ts'

// pairvoice（profiles.py）のプロファイル ID と同じ形
const PROFILE_ID_PATTERN = /^p-[0-9A-Za-z-]+$/

// ミュートの切り替えは pairvoice が即答する。待たせ続けるより、止まっていると早く伝える
const MUTE_TIMEOUT_MS = 3000

// pairvoice に中継する。繋がらない・時間切れは、studio 自身の失敗（500）と
// 区別できるよう 502 で申告する。画面は message を出す。クライアントが先に切った
// 場合は返す先が無いので何もしない。timeoutMs を省くと待ち続ける（試聴は合成の列で
// フックの読み上げやモデルの読み込み・ダウンロードの後ろに並び、何分かかるか読めない。
// 打ち切ると、動いている pairvoice を「起動していない」と申告してしまう）
async function forwardToPairvoice(
  res: http.ServerResponse,
  target: string,
  init: RequestInit,
  timeoutMs?: number,
) {
  // 待っている間にクライアントが切ったら、pairvoice への要求も切る。待ち続ける接続を
  // 残さないためで、pairvoice 側の合成は止まらない（Runner の列に入った分は最後まで走る）
  const clientGone = new AbortController()
  const onClose = () => {
    if (!res.writableFinished) clientGone.abort()
  }
  res.on('close', onClose)
  try {
    const response = await fetch(`${pairvoiceBase()}${target}`, {
      ...init,
      signal:
        timeoutMs === undefined
          ? clientGone.signal
          : AbortSignal.any([clientGone.signal, AbortSignal.timeout(timeoutMs)]),
    })
    const contentType = response.headers.get('content-type') ?? ''
    if (contentType.startsWith('audio/') && response.body) {
      res.writeHead(response.status, {
        'Content-Type': contentType,
        ...(response.headers.has('content-length') && {
          'Content-Length': response.headers.get('content-length')!,
        }),
      })
      // 書き始めた後の失敗は伝えようがないので捨てる
      await pipeline(Readable.fromWeb(response.body), res).catch(() => {})
      return
    }
    await relayPairvoice(res, response, target)
  } catch (err) {
    if (clientGone.signal.aborted || res.headersSent) return
    const reason =
      isTimeout(err) && timeoutMs !== undefined
        ? `${timeoutMs / 1000} 秒以内に応答がありませんでした`
        : '接続できませんでした'
    sendJson(res, 502, {
      error: 'pairvoice_unreachable',
      message: `pairvoice ${target} に${reason}。pairvoice が起動しているか確認してください。`,
    })
  } finally {
    res.off('close', onClose)
  }
}

function postJson(payload: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

// データの置き場所の読み書きは pairvoice の API が持つ（studio はファイルに触らない）。
// パスから /api を外し、クエリも本文もそのまま中継する
const RELAYED_ROUTES = [
  ['GET', '/prompt'],
  ['PUT', '/prompt'],
  ['GET', '/prompt/history'],
  ['POST', '/prompt/restore'],
  ['GET', '/dict'],
  ['PUT', '/dict'],
  ['POST', '/dict/test'],
  ['GET', '/corpus'],
  ['GET', '/corpus/:id/audio'],
  ['POST', '/reviews'],
  ['POST', '/archives'],
  ['POST', '/archives/bulk'],
  ['GET', '/audio'],
  ['GET', '/styles'],
  ['PUT', '/styles'],
  ['GET', '/profiles'],
  ['POST', '/profiles'],
  ['POST', '/profiles/upload'],
  ['PUT', '/profiles/active'],
  ['PATCH', '/profiles/:id'],
  ['DELETE', '/profiles/:id'],
  ['GET', '/profiles/:id/audio'],
  ['GET', '/profiles/:id/caption/history'],
  ['POST', '/profiles/:id/caption/restore'],
] as const

function relayInit(req: http.IncomingMessage): RequestInit {
  const contentType = req.headers['content-type']
  const hasBody = req.method !== 'GET' && req.method !== 'DELETE'
  return {
    method: req.method,
    ...(contentType && { headers: { 'Content-Type': contentType } }),
    // 本文は読み溜めずに流す（声の wav は数十 MB になる）
    ...(hasBody && { body: Readable.toWeb(req), duplex: 'half' }),
  }
}

type MixPart = { audio: string; weight: number }

function isMixPart(part: unknown): part is MixPart {
  return isRecord(part) && typeof part.audio === 'string' && typeof part.weight === 'number'
}

function isTimeout(err: unknown) {
  return err instanceof DOMException && err.name === 'TimeoutError'
}

export function registerPairvoiceRoutes(addRoute: AddRoute) {
  for (const [method, pattern] of RELAYED_ROUTES) {
    addRoute(method, `/api${pattern}`, (req, res) =>
      // http.Server が渡すリクエストには url が必ず入る（型の上でだけ optional）
      forwardToPairvoice(res, req.url!.slice('/api'.length), relayInit(req)),
    )
  }

  // 試聴。ブラウザから直接 :17495 を叩くとCORSの考慮が要るため studio 経由で中継する。
  addRoute('POST', '/api/speak', async (req, res) => {
    const body = await readJsonBody(req)
    if (typeof body.text !== 'string' || body.text.trim() === '') {
      return badRequest(res, 'text (non-empty string) is required')
    }
    const payload: {
      text: string
      caption?: string
      sampler?: Record<string, unknown>
      design?: true
      voice?: string
      mix?: { audio: string; weight: number }[]
    } = {
      text: body.text,
    }
    // プロファイル作成の候補づくり。参照音声を使わず caption だけで声を作る。
    // 既定（false）はキーごと送らない
    if (body.design === true) payload.design = true
    // 使用中でないプロファイルの声で試聴する。省くと使用中のプロファイルで鳴る。
    // 形の違う ID を黙って落とすと、使用中の声で鳴って取り違えに気づけない
    if (body.profile_id !== undefined) {
      if (typeof body.profile_id !== 'string' || !PROFILE_ID_PATTERN.test(body.profile_id)) {
        return badRequest(res, 'invalid profile_id')
      }
      payload.voice = body.profile_id
    }
    // 2択で絞り込むときの、もとの声を重みで混ぜた声。パスの検証は pairvoice が行う
    // （データの置き場所の外を指していれば 404）
    if (body.mix !== undefined) {
      if (!Array.isArray(body.mix) || body.mix.length === 0 || !body.mix.every(isMixPart)) {
        return badRequest(res, 'mix must be a non-empty array of {audio, weight}')
      }
      payload.mix = body.mix.map(({ audio, weight }: MixPart) => ({ audio, weight }))
    }
    // caption 未指定は「プロファイルの caption で読む」、空文字は「caption なしで読む」の意味。
    // 文字列でなければ送らない
    if (typeof body.caption === 'string') payload.caption = body.caption.trim()
    // sampler はキーを検査せず素通しする。正しさは pairvoice 側の extra="forbid" が
    // 422 で申告する。許可キーの一覧を studio に写すと項目を増やすたび2箇所を直す
    // ことになり、片方を忘れた分は黙って落ちる。
    // 空のオブジェクトは caption と同じ理由でキーごと送らない
    if (
      body.sampler !== null &&
      typeof body.sampler === 'object' &&
      !Array.isArray(body.sampler) &&
      Object.keys(body.sampler).length > 0
    ) {
      payload.sampler = body.sampler
    }
    // 成功時は SpeakResponse の形だが、pairvoice 側のバリデーションエラー等では
    // この型に無い形（detail/error）で返ることがある。中身は検証せず素通しする
    // （クライアント側が防御的に読む）。
    await forwardToPairvoice(res, '/synthesize', postJson(payload))
  })

  // 2択で絞り込むときに、もとの声どうしの位置を測る話者ベクトル
  addRoute('POST', '/api/speaker-vector', async (req, res) => {
    const body = await readJsonBody(req)
    if (typeof body.audio !== 'string' || body.audio === '') {
      return badRequest(res, 'audio (non-empty string) is required')
    }
    await forwardToPairvoice(res, '/speaker-vector', postJson({ audio: body.audio }))
  })

  addRoute('GET', '/api/health', async (req, res) => {
    let pairvoice: PairvoiceHealth | null = null
    try {
      const response = await fetch(`${pairvoiceBase()}/health`, {
        signal: AbortSignal.timeout(2000),
      })
      // pairvoice の応答は検証せずそのまま中継する。studio が形を判断すると、
      // pairvoice 側に項目が増えたときに studio の更新が必要になってしまう。
      // oxlint-disable-next-line typescript/no-unsafe-type-assertion -- 検証しないのが意図（上記）
      if (response.ok) pairvoice = (await response.json()) as PairvoiceHealth
    } catch {
      pairvoice = null // 未起動。UI 側で「停止」として扱う
    }
    const body: StudioHealth = { pairvoice }
    sendJson(res, 200, body)
  })

  // ブラウザから直接 :17495 を叩くとCORSの考慮が要るため、studio経由で中継する。
  // minutes未指定(null/undefined)は解除、指定ありはその分数のミュートとして扱う。
  addRoute('POST', '/api/mute', async (req, res) => {
    const body = await readJsonBody(req)
    // target選択とペイロード組み立てを同じ判定基準(!= null)に統一する。
    // 片方だけtruthy判定だと minutes:0 のような値でtargetとbodyが食い違う事故になる。
    const hasMinutes = body.minutes != null
    const target = hasMinutes ? '/mute' : '/unmute'
    const payload = hasMinutes ? { minutes: body.minutes } : {}
    await forwardToPairvoice(res, target, postJson(payload), MUTE_TIMEOUT_MS)
  })
}
