import type http from 'node:http'

import type { PairvoiceHealth, StudioHealth } from '../../shared/api-types.ts'
import {
  badRequest,
  isRecord,
  notFound,
  readJsonBody,
  relayPairvoice,
  sendJson,
  streamWav,
} from '../http.ts'
import { pairvoiceBase } from '../paths.ts'
import type { AddRoute } from '../router.ts'
import { resolveAudioPath } from '../storage.ts'
import { PROFILE_ID_PATTERN } from './profiles.ts'

// ミュートの切り替えは pairvoice が即答する。待たせ続けるより、止まっていると早く伝える
const MUTE_TIMEOUT_MS = 3000

// POST で pairvoice に中継する。繋がらない・時間切れは、studio 自身の失敗（500）と
// 区別できるよう 502 で申告する。画面は message を出す。クライアントが先に切った
// 場合は返す先が無いので何もしない。timeoutMs を省くと待ち続ける（試聴は合成の列で
// フックの読み上げやモデルの読み込み・ダウンロードの後ろに並び、何分かかるか読めない。
// 打ち切ると、動いている pairvoice を「起動していない」と申告してしまう）
async function forwardToPairvoice(
  res: http.ServerResponse,
  target: string,
  payload: unknown,
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
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal:
        timeoutMs === undefined
          ? clientGone.signal
          : AbortSignal.any([clientGone.signal, AbortSignal.timeout(timeoutMs)]),
    })
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

type MixPart = { audio: string; weight: number }

function isMixPart(part: unknown): part is MixPart {
  return isRecord(part) && typeof part.audio === 'string' && typeof part.weight === 'number'
}

function isTimeout(err: unknown) {
  return err instanceof DOMException && err.name === 'TimeoutError'
}

export function registerPairvoiceRoutes(addRoute: AddRoute) {
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
    await forwardToPairvoice(res, '/synthesize', payload)
  })

  // 2択で絞り込むときに、もとの声どうしの位置を測る話者ベクトル
  addRoute('POST', '/api/speaker-vector', async (req, res) => {
    const body = await readJsonBody(req)
    if (typeof body.audio !== 'string' || body.audio === '') {
      return badRequest(res, 'audio (non-empty string) is required')
    }
    await forwardToPairvoice(res, '/speaker-vector', { audio: body.audio })
  })

  // 試聴で生成された音声は corpus.jsonl に載らないので /api/audio/:message_id では
  // 配信できない。相対パスの検証（配ってよい場所に絞る）は resolveAudioPath 側。
  addRoute('GET', '/api/audio-file', async (req, res, ctx) => {
    const relative = ctx.query.get('path')
    if (!relative) return badRequest(res, 'path is required')

    const resolved = await resolveAudioPath(relative)
    if (!resolved) return notFound(res)

    await streamWav(res, resolved)
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
    await forwardToPairvoice(res, target, payload, MUTE_TIMEOUT_MS)
  })
}
