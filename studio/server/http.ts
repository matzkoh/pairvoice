import fsp from 'node:fs/promises'
import type http from 'node:http'
import { pipeline } from 'node:stream/promises'

// JSON から読んだ値を「キーを引ける形」に絞る。pairvoice の応答のように、形が
// このリポジトリの外で決まるものを読むときに使う。
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function sendJson(res: http.ServerResponse, status: number, obj: unknown) {
  const body = JSON.stringify(obj)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  })
  res.end(body)
}

export function notFound(res: http.ServerResponse) {
  sendJson(res, 404, { error: 'not_found' })
}

export function badRequest(res: http.ServerResponse, message: string) {
  sendJson(res, 400, { error: 'bad_request', message })
}

// pairvoice の応答をそのまま中継する（POST の proxy 2本が使う）。中身は検証せず
// 素通しするが、JSON とも限らない: pairvoice が例外で 500 になったときの本文は
// uvicorn の `Internal Server Error`（text/plain）で、response.json() はそこで構文
// エラーを投げる。すると addRoute の catch が studio 自身の internal_error として
// 返し、画面には原因と無関係な「Unexpected token 'I'」が出る。読めない本文は
// { error, message } に包んで添える（クライアントはこの形を前提に読む）。
export async function relayPairvoice(res: http.ServerResponse, response: Response, label: string) {
  const raw = await response.text()
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    // catch の中に sendJson を置かない（応答の組み立てで投げた分まで拾って二重に
    // 書き始めてしまう）
    return sendJson(res, response.status, {
      error: 'pairvoice_unreadable_response',
      message: `pairvoice ${label} が ${response.status} を返しました: ${raw.slice(0, 200).trim()}`,
    })
  }
  sendJson(res, response.status, parsed)
}

// 本文が上限を超えた・JSON として読めない。どちらも要求側の誤りなので、ルーターが
// 500 ではなく 413 / 400 に変える
export class BodyTooLargeError extends Error {}
export class InvalidJsonError extends Error {}

// JSON の本文の上限。プロンプトや辞書の全置換で足りる
const JSON_BODY_MAX_BYTES = 10 * 1024 * 1024

// 上限は読みながら数える。Content-Length の事前確認だけでは、chunked で送られた
// 本文を青天井でメモリに積む
export function readBodyBuffer(req: http.IncomingMessage, maxBytes: number) {
  return new Promise<Buffer>((resolve, reject) => {
    const chunks: Buffer[] = []
    let size = 0
    const onData = (c: Buffer) => {
      size += c.length
      if (size > maxBytes) {
        // 残りは貯めない（ルーターが 413 を返して読み捨てる）
        req.off('data', onData)
        reject(new BodyTooLargeError(`body exceeds ${maxBytes} bytes`))
        return
      }
      chunks.push(c)
    }
    req.on('data', onData)
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

export async function readBody(req: http.IncomingMessage, maxBytes = JSON_BODY_MAX_BYTES) {
  return (await readBodyBuffer(req, maxBytes)).toString('utf8')
}

// 無い・ファイルでないなら 404。音声を返す経路（コーパス・試聴・プロファイル）はすべてここを通る。
// 開いてから応答を書き始める。stat と open の間に消される（maintain() の掃除やプロファイルの
// 削除）・読めない場合に、200 を書いた後で ReadStream の 'error' が誰にも拾われず落ちるため
export async function streamWav(res: http.ServerResponse, file: string) {
  let handle
  try {
    handle = await fsp.open(file, 'r')
  } catch (err) {
    if (isRecord(err) && (err.code === 'ENOENT' || err.code === 'ENOTDIR')) return notFound(res)
    return sendJson(res, 500, { error: 'audio_unreadable', message: String(err) })
  }
  const stat = await handle.stat().catch(() => null)
  if (!stat?.isFile()) {
    await handle.close()
    return notFound(res)
  }

  res.writeHead(200, { 'Content-Type': 'audio/wav', 'Content-Length': stat.size })
  // pipeline は失敗やクライアントの切断で両端を壊し、ファイルも閉じる。応答は書き始めて
  // いるので、ここでの失敗は伝えようがなく、捨てる
  await pipeline(handle.createReadStream(), res).catch(() => {})
}

// JSON.parse の戻りを返す。ボディの形は経路ごとに違い、各ハンドラが必要な
// フィールドだけを typeof で確かめてから使う（ここで一律に絞ると、経路ごとの検証と
// 400 の文言を型の側にもう一度書くことになる）。ただし null や配列はどの経路でも
// フィールドを引けないので、ここで断る
export async function readJsonBody(req: http.IncomingMessage): Promise<any> {
  const raw = await readBody(req)
  if (!raw) return {}
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (err) {
    throw new InvalidJsonError(String(err))
  }
  if (!isRecord(parsed) || Array.isArray(parsed)) {
    throw new InvalidJsonError('body must be a JSON object')
  }
  return parsed
}
