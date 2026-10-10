import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  StaleServerError,
  UnreachableServerError,
  apiGet,
  apiGetBlob,
  apiSend,
} from './api'

function mockFetch(status: number, body: string, contentType = 'application/json') {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(new Response(body, { status, headers: { 'content-type': contentType } }))
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('apiGet', () => {
  it('JSON を返す', async () => {
    mockFetch(200, '{"ok":true}')
    await expect(apiGet<{ ok: boolean }>('/health')).resolves.toEqual({ ok: true })
  })

  it('404 は StaleServerError になる', async () => {
    mockFetch(404, '{"error":"not_found"}')
    const err = await apiGet('/caption').catch((e: unknown) => e)
    // 404 は「そのルートがまだ無い」= サーバーが古い、の意味で扱う。
    // 今日の不具合はまさにこれで、画面が黙って固まった。
    expect(err).toBeInstanceOf(StaleServerError)
    expect((err as StaleServerError).status).toBe(404)
    expect((err as StaleServerError).message).toContain('再起動')
  })

  it('404 以外は ApiError になり、本文を持つ', async () => {
    mockFetch(409, '{"error":"profile_in_use"}')
    const err = await apiGet('/profiles').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).not.toBeInstanceOf(StaleServerError)
    expect((err as ApiError).status).toBe(409)
    // 画面のインライン表示に出す文字列なので、本文を落とさない。
    expect((err as ApiError).body).toContain('profile_in_use')
  })

  it('本文に message があれば Error.message にそれを使う（生 JSON を出さない）', async () => {
    // message があればそれを使う。
    // message は人間向けの説明なので、生 JSON より優先する。
    mockFetch(400, '{"error":"bad_request","message":"text (non-empty string) is required"}')
    const err = await apiGet('/prompt').catch((e: unknown) => e)
    expect((err as ApiError).message).toBe('text (non-empty string) is required')
    // body は生のまま保持する（既存のアサーションを壊さない）。
    expect((err as ApiError).body).toContain('bad_request')
  })

  it('message が無ければ error のコードを Error.message に使う', async () => {
    // pairvoice の応答は { error } だけのことがある。
    mockFetch(409, '{"error":"model_busy"}')
    const err = await apiGet('/synthesize').catch((e: unknown) => e)
    expect((err as ApiError).message).toBe('model_busy')
  })

  it('JSON として読めない本文は今までどおりのフォールバック形式になる', async () => {
    mockFetch(500, '<html>Internal Server Error</html>', 'text/html')
    const err = await apiGet('/prompt').catch((e: unknown) => e)
    expect((err as ApiError).message).toBe('/prompt: 500 <html>Internal Server Error</html>')
  })

  it('JSON でない応答は文字列で返す', async () => {
    mockFetch(200, 'plain text', 'text/plain')
    await expect(apiGet<string>('/whatever')).resolves.toBe('plain text')
  })

  it('allowNotFound を渡すと 404 が StaleServerError にならず普通の ApiError になる', async () => {
    // GET /audio/... は音声ファイルが無いと 404 を返すが、これは「サーバーが古い」
    // という意味ではなく正当な業務上の答え。既定を変えず、この呼び出しだけ
    // オプトアウトできることを確かめる。
    mockFetch(404, '{"error":"not_found"}')
    const err = await apiGet('/audio/x', { allowNotFound: true }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).not.toBeInstanceOf(StaleServerError)
    expect((err as ApiError).status).toBe(404)
  })

  it('allowNotFound が無い既定の呼び出しは今までどおり StaleServerError のまま', async () => {
    // オプトアウトを足しても既存の呼び出し（opts省略）の挙動が変わっていないことの確認。
    mockFetch(404, '{"error":"not_found"}')
    const err = await apiGet('/prompt').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(StaleServerError)
  })

  it('fetch が reject したら UnreachableServerError になる', async () => {
    // studio のプロセスが落ちている場合。応答が返ってくる 404 とは別の障害で、
    // ブラウザの 'Failed to fetch' では何をすればいいか読み取れない。
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    const err = await apiGet('/health').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(UnreachableServerError)
    // HTTP 応答が無いので ApiError（status を持つ）ではない。
    expect(err).not.toBeInstanceOf(ApiError)
    expect((err as UnreachableServerError).message).toContain('接続できませんでした')
    expect((err as UnreachableServerError).message).toContain('再起動')
    expect((err as UnreachableServerError).path).toBe('/health')
    // 原因を捨てない。
    expect((err as UnreachableServerError).cause).toBeInstanceOf(TypeError)
  })
})

describe('apiGetBlob', () => {
  it('成功時は blob を返す（JSON/text に変換しない）', async () => {
    mockFetch(200, 'binary-ish', 'audio/wav')
    const blob = await apiGetBlob('/audio/x')
    expect(blob).toBeInstanceOf(Blob)
    expect(blob.type).toBe('audio/wav')
  })

  it('既定では 404 が StaleServerError になる', async () => {
    mockFetch(404, '{"error":"not_found"}')
    const err = await apiGetBlob('/audio/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(StaleServerError)
  })

  it('allowNotFound を渡すと 404 が普通の ApiError になる', async () => {
    mockFetch(404, '{"error":"not_found"}')
    const err = await apiGetBlob('/audio/x', { allowNotFound: true }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).not.toBeInstanceOf(StaleServerError)
    expect((err as ApiError).status).toBe(404)
  })
})

describe('apiSend', () => {
  it('JSON body を送る', async () => {
    const spy = mockFetch(200, '{"ok":true}')
    await apiSend('/caption', 'PUT', { text: 'あ' })
    const [, init] = spy.mock.calls[0]!
    expect(init?.method).toBe('PUT')
    expect(init?.body).toBe('{"text":"あ"}')
    expect(new Headers(init?.headers).get('content-type')).toBe('application/json')
  })

  it('body 無しなら Content-Type を付けない', async () => {
    const spy = mockFetch(200, '{"ok":true}')
    await apiSend('/prompt/restore', 'POST')
    const [, init] = spy.mock.calls[0]!
    expect(init?.body).toBeUndefined()
    expect(new Headers(init?.headers).get('content-type')).toBeNull()
  })

  it('fetch が reject したら UnreachableServerError になる', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    const err = await apiSend('/mute', 'POST', { minutes: 30 }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(UnreachableServerError)
    expect((err as UnreachableServerError).path).toBe('/mute')
  })
})
