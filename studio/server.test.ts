import assert from 'node:assert/strict'
import fs from 'node:fs'
import { rename, rm } from 'node:fs/promises'
import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import { test } from 'node:test'

import type { StudioHealth } from './shared/api-types.ts'

// 疎通不可なポートに固定し、pairvoice未起動時の挙動を毎回再現する
process.env.PAIRVOICE_URL = 'http://127.0.0.1:1'

const { startServer, STUDIO_DIR } = await import('./server.ts')

// listen 済みの TCP サーバーなので address() は AddressInfo（型の上では string | null も
// ありうる）。テストは実ポートを URL に組むためだけに読むので、絞り込みをここに集める。
function portOf(server: http.Server): number {
  const address = server.address()
  assert.ok(address !== null && typeof address !== 'string', 'listen 済みのサーバーではない')
  return address.port
}

// fetch の json() は unknown を返す。応答の形を確かめるのがテストの仕事なので、
// 取り出しごとに絞らず any として読む（絞ると assert の前に型の分岐が挟まり、
// 何を確かめているのかが読めなくなる）。
async function readJson(res: Response): Promise<any> {
  return res.json()
}

function jsonInit(method: string, body: unknown) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

// /api/mute の中継先を一時的に差し替えるための偽pairvoice。受けたリクエストを
// method/url/bodyで記録するだけで、実際の17495やsettings.jsonには一切依存しない。
async function startFakePairvoice() {
  const requests: { method?: string; url?: string; body: any }[] = []
  const server = http.createServer((req, res) => {
    let raw = ''
    req.on('data', (chunk) => {
      raw += chunk
    })
    req.on('end', () => {
      requests.push({ method: req.method, url: req.url, body: raw ? JSON.parse(raw) : null })
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ ok: true }))
    })
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
  const port = portOf(server)
  return {
    requests,
    url: `http://127.0.0.1:${port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}

test('/api/health は pairvoice が停止していても 200 を返す', async () => {
  const server = await startServer(0)
  const port = portOf(server)
  const res = await fetch(`http://127.0.0.1:${port}/api/health`)
  assert.equal(res.status, 200)
  const body: StudioHealth = await readJson(res)
  // PAIRVOICE_URL は疎通不可なポートに固定してあるため常にnull
  assert.equal(body.pairvoice, null)
  await new Promise((resolve) => server.close(resolve))
})

test('POST /api/mute は pairvoice が停止していてもサーバーをクラッシュさせない', async () => {
  const server = await startServer(0)
  const port = portOf(server)
  const res = await fetch(`http://127.0.0.1:${port}/api/mute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ minutes: 30 }),
  })
  // pairvoice未起動時は 502 で申告する
  // （route内でクラッシュしないことの確認。以後の要求にも支障が出ないことを見る）
  assert.equal(res.status, 502)

  const followUp = await fetch(`http://127.0.0.1:${port}/api/health`)
  assert.equal(followUp.status, 200)
  await new Promise((resolve) => server.close(resolve))
})

test('POST /api/mute は minutes 指定時に /mute へ、null 指定時に /unmute へ中継する', async () => {
  const fake = await startFakePairvoice()
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const port = portOf(server)

    const muteRes = await fetch(`http://127.0.0.1:${port}/api/mute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: 30 }),
    })
    assert.equal(muteRes.status, 200)

    const unmuteRes = await fetch(`http://127.0.0.1:${port}/api/mute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: null }),
    })
    assert.equal(unmuteRes.status, 200)

    assert.equal(fake.requests.length, 2)
    assert.equal(fake.requests[0]!.url, '/mute')
    assert.deepEqual(fake.requests[0]!.body, { minutes: 30 })
    assert.equal(fake.requests[1]!.url, '/unmute')
    assert.deepEqual(fake.requests[1]!.body, {})
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl // 以後のテストは疎通不可な固定値に戻す
    await fake.close()
  }
})

test('unknown /api route returns 404 JSON', async () => {
  const server = await startServer(0)
  const port = portOf(server)
  const res = await fetch(`http://127.0.0.1:${port}/api/does-not-exist`)
  assert.equal(res.status, 404)
  const body = await readJson(res)
  assert.equal(body.error, 'not_found')
  await new Promise((resolve) => server.close(resolve))
})

test('壊れた URL は 400 で断り、サーバーを落とさない', async () => {
  const server = await startServer(0)
  const port = portOf(server)
  try {
    for (const bad of ['/%E0%A4%A', '/api/%E0%A4%A']) {
      const res = await fetch(`http://127.0.0.1:${port}${bad}`)
      assert.equal(res.status, 400)
      assert.equal((await readJson(res)).error, 'bad_path')
    }
    // fetch は絶対形式の要求行を送れないので、ソケットに直接書く
    const raw = await new Promise<string>((resolve, reject) => {
      const socket = net.connect(port, '127.0.0.1', () => {
        socket.end('GET http://[x HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
      })
      let data = ''
      socket.on('data', (chunk) => (data += chunk))
      socket.on('end', () => resolve(data))
      socket.on('error', reject)
    })
    assert.match(raw, /^HTTP\/1\.1 400 /)
    const res = await fetch(`http://127.0.0.1:${port}/api/does-not-exist`)
    assert.equal(res.status, 404)
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
})

import { mkdir, writeFile } from 'node:fs/promises'

// DIST_DIR（studio/web/dist）は pnpm build が作る成果物で、開発中や CI の
// 実行順序次第では存在したりしなかったりする。以下のテストは「無い状態」
// 「フィクスチャがある状態」をそれぞれ hermetic に作るため、実体があれば
// 退避してから復元する（pnpm build の結果を壊さない）。
async function withDist(setup: (() => Promise<void>) | null, run: () => Promise<void>) {
  const { DIST_DIR } = await import('./server.ts')
  const backup = `${DIST_DIR}.bak-${process.pid}-${Date.now()}`
  const existed = fs.existsSync(DIST_DIR)
  if (existed) await rename(DIST_DIR, backup)
  try {
    if (setup) await setup()
    await run()
  } finally {
    await rm(DIST_DIR, { recursive: true, force: true })
    if (existed) await rename(backup, DIST_DIR)
  }
}

test('dist が無いときは 500 ではなく手順を書いた案内を返す', async () => {
  // 開発中に :17494 を直接開くと必ずこの経路を通る。無言で壊れると
  // 「読み込み中…で固まる」のと同じ迷い方をするので、次にやることを書く。
  await withDist(null, async () => {
    const server = await startServer(0)
    try {
      const res = await fetch(`http://127.0.0.1:${portOf(server)}/`)
      assert.equal(res.status, 503)
      const html = await res.text()
      assert.match(html, /pnpm dev/)
      assert.match(html, /pnpm build/)
    } finally {
      server.close()
    }
  })
})

test('静的ファイルはコードの置き場所から配信する', async () => {
  const { DIST_DIR } = await import('./server.ts')
  assert.equal(DIST_DIR, path.join(STUDIO_DIR, 'web', 'dist'))

  await withDist(
    async () => {
      await mkdir(DIST_DIR, { recursive: true })
      await writeFile(
        path.join(DIST_DIR, 'index.html'),
        '<!doctype html><title>dist-fixture</title>',
        'utf8',
      )
    },
    async () => {
      const server = await startServer(0)
      try {
        const base = `http://127.0.0.1:${portOf(server)}`
        // index.html はリポジトリ側の studio/web/dist から配信される
        const page = await fetch(`${base}/`)
        assert.equal(page.status, 200)
        assert.match(await page.text(), /dist-fixture/)
      } finally {
        server.close()
      }
    },
  )
})

test('SPA なので実ファイルに無いパスも index.html を返す（/review を直接開けるように）', async () => {
  const { DIST_DIR } = await import('./server.ts')
  await withDist(
    async () => {
      await mkdir(DIST_DIR, { recursive: true })
      await writeFile(
        path.join(DIST_DIR, 'index.html'),
        '<!doctype html><title>dist-fixture</title>',
        'utf8',
      )
    },
    async () => {
      const server = await startServer(0)
      try {
        const base = `http://127.0.0.1:${portOf(server)}`
        const page = await fetch(`${base}/review`)
        assert.equal(page.status, 200)
        assert.match(await page.text(), /dist-fixture/)

        // /api/ 配下は SPA フォールバックに飲まれず、404 として返る
        const api = await fetch(`${base}/api/does-not-exist`)
        assert.equal(api.status, 404)
      } finally {
        server.close()
      }
    },
  )
})

// 応答を丸ごと決められる偽 pairvoice。startFakePairvoice は固定の {ok:true} を返すため、
// 申告の中身や JSON でない応答を見るテストには使えない。
async function startFakeUpstream(handler: http.RequestListener) {
  const server = http.createServer(handler)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
  return {
    url: `http://127.0.0.1:${portOf(server)}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}

test('POST /api/speak は pairvoice の JSON でない 500 を読める形で申告する', async () => {
  // pairvoice が合成中の例外で落ちたときの本文（uvicorn の text/plain）を模す。
  // ここで JSON として読もうとすると studio 自身の構文エラーが中継され、画面には
  // 原因と無関係な「Unexpected token 'I'」だけが出る
  const fake = await startFakeUpstream((_req, res) => {
    res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('Internal Server Error')
  })
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const res = await fetch(`http://127.0.0.1:${portOf(server)}/api/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'テスト' }),
    })
    assert.equal(res.status, 500)
    const body = await readJson(res)
    assert.equal(body.error, 'pairvoice_unreadable_response')
    // 読めなかった本文が画面まで届くことが要点。文言そのものは固定しない
    assert.match(body.message, /Internal Server Error/)
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

test('POST /api/speak は design と profile_id（voice として）を pairvoice へ中継する', async () => {
  const fake = await startFakePairvoice()
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const port = portOf(server)
    await fetch(`http://127.0.0.1:${port}/api/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: '候補', caption: '試す声。', design: true }),
    })
    assert.deepEqual(fake.requests[0]!.body, {
      text: '候補',
      caption: '試す声。',
      design: true,
    })
    // 試聴するプロファイルは ID の形のものだけを中継し、ほかは 400 で断る
    const ok = await fetch(
      `http://127.0.0.1:${port}/api/speak`,
      jsonInit('POST', { text: '試聴', profile_id: 'p-other' }),
    )
    assert.equal(ok.status, 200)
    assert.equal(fake.requests[1]!.body.voice, 'p-other')
    const bad = await fetch(
      `http://127.0.0.1:${port}/api/speak`,
      jsonInit('POST', { text: '試聴', profile_id: '../x' }),
    )
    assert.equal(bad.status, 400)
    assert.equal(fake.requests.length, 2)
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

test('POST /api/speak は mix を、POST /api/speaker-vector は audio を pairvoice へ中継する', async () => {
  const fake = await startFakePairvoice()
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const mix = [
      { audio: 'generations/a.wav', weight: 0.25 },
      { audio: 'generations/b.wav', weight: 0.75 },
    ]
    const ok = await fetch(
      `http://127.0.0.1:${port}/api/speak`,
      jsonInit('POST', { text: '候補', caption: '女性の声。', mix }),
    )
    assert.equal(ok.status, 200)
    assert.deepEqual(fake.requests[0]!.body.mix, mix)
    // 形の崩れた mix は一部だけ落として送らず、まとめて断る
    for (const bad of [[], [{ audio: 'generations/a.wav' }], 'x']) {
      const res = await fetch(
        `http://127.0.0.1:${port}/api/speak`,
        jsonInit('POST', { text: '候補', mix: bad }),
      )
      assert.equal(res.status, 400)
    }
    assert.equal(fake.requests.length, 1)

    await fetch(
      `http://127.0.0.1:${port}/api/speaker-vector`,
      jsonInit('POST', { audio: 'generations/a.wav' }),
    )
    assert.equal(fake.requests[1]!.url, '/speaker-vector')
    assert.deepEqual(fake.requests[1]!.body, { audio: 'generations/a.wav' })
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

test('POST /api/speak は text と caption を pairvoice の /synthesize へ中継する', async () => {
  const fake = await startFakePairvoice()
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const res = await fetch(`http://127.0.0.1:${port}/api/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'テスト', caption: '試している声。' }),
    })
    assert.equal(res.status, 200)
    assert.equal(fake.requests.length, 1)
    assert.equal(fake.requests[0]!.url, '/synthesize')
    assert.deepEqual(fake.requests[0]!.body, {
      text: 'テスト',
      caption: '試している声。',
    })
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

test('POST /api/speak は text が空なら 400 を返す', async () => {
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const res = await fetch(`http://127.0.0.1:${port}/api/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: '  ', caption: '声。' }),
    })
    assert.equal(res.status, 400)
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
})

test('POST /api/speak は caption を省略したときペイロードに caption キーを含めない', async () => {
  const fake = await startFakePairvoice()
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const res = await fetch(`http://127.0.0.1:${port}/api/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'テスト' }),
    })
    assert.equal(res.status, 200)
    assert.equal(fake.requests.length, 1)
    // caption 未指定は「採用済みの版で読む」の意味。キーごと送らない
    assert.ok(!('caption' in fake.requests[0]!.body), 'caption キーは含まれるべきではない')
    assert.deepEqual(fake.requests[0]!.body, { text: 'テスト' })
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

test('POST /api/speak は空白のみの caption を空の caption として中継する', async () => {
  const fake = await startFakePairvoice()
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const res = await fetch(`http://127.0.0.1:${port}/api/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'テスト', caption: '  　\t' }),
    })
    assert.equal(res.status, 200)
    assert.equal(fake.requests.length, 1)
    // 空は「caption なしで読む」の指定。省略（プロファイルの caption で読む）とは区別する
    assert.deepEqual(fake.requests[0]!.body, { text: 'テスト', caption: '' })
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

test('POST /api/speak は sampler を pairvoice へ素通しで中継する', async () => {
  const fake = await startFakePairvoice()
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const res = await fetch(`http://127.0.0.1:${port}/api/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'テスト', sampler: { num_steps: 60, rng_seed: 7 } }),
    })
    assert.equal(res.status, 200)
    assert.deepEqual(fake.requests[0]!.body, {
      text: 'テスト',
      sampler: { num_steps: 60, rng_seed: 7 },
    })
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

test('POST /api/speak は sampler が無いときペイロードにキーを含めない', async () => {
  const fake = await startFakePairvoice()
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const res = await fetch(`http://127.0.0.1:${port}/api/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'テスト', sampler: {} }),
    })
    assert.equal(res.status, 200)
    // 空の sampler は「上書きなし」。キーごと送らないと pairvoice 側で
    // 「指定あり・全部 None」と区別がつかなくなる
    assert.ok(!('sampler' in fake.requests[0]!.body), 'sampler キーは含まれるべきではない')
  } finally {
    await new Promise((resolve) => server.close(resolve))
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

// node の fetch は Host を上書きできないので、http.request で指定して叩く
function requestWithHeaders(
  port: number,
  pathname: string,
  headers: Record<string, string>,
  method = 'GET',
) {
  return new Promise<number>((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port, path: pathname, method, headers, setHost: false },
      (res) => {
        res.resume()
        res.on('end', () => resolve(res.statusCode ?? 0))
      },
    )
    req.on('error', reject)
    req.end()
  })
}

test('ローカルの名前でない Host は静的配信も含めて 403 にする（DNS rebinding 対策）', async () => {
  const server = await startServer(0)
  try {
    const port = portOf(server)
    for (const host of ['evil.example', `evil.example:${port}`, '127.0.0.1.evil.example']) {
      assert.equal(await requestWithHeaders(port, '/api/dict', { host }), 403, `api: ${host}`)
      assert.equal(await requestWithHeaders(port, '/', { host }), 403, `static: ${host}`)
    }
  } finally {
    server.close()
  }
})

test('Host が 127.0.0.1 / localhost / [::1] なら、ポートが何でも通す', async () => {
  const server = await startServer(0)
  try {
    const port = portOf(server)
    for (const host of [
      '127.0.0.1',
      '127.0.0.1:17493',
      'localhost:5173',
      'LOCALHOST',
      '[::1]:8080',
    ]) {
      assert.equal(await requestWithHeaders(port, '/api/health', { host }), 200, host)
    }
  } finally {
    server.close()
  }
})

test('外部のページの Origin は、Host がローカルでも 403 にする（単純な POST 対策）', async () => {
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const host = `127.0.0.1:${port}`
    for (const origin of ['https://evil.example', 'null', 'http://127.0.0.1.evil.example']) {
      assert.equal(
        await requestWithHeaders(port, '/api/prompt', { host, origin }, 'PUT'),
        403,
        origin,
      )
    }
    assert.equal(
      await requestWithHeaders(port, '/api/health', { host, origin: 'http://localhost:17493' }),
      200,
    )
  } finally {
    server.close()
  }
})

async function withServer(fn: (base: string) => Promise<void>) {
  const server = await startServer(0)
  try {
    await fn(`http://127.0.0.1:${portOf(server)}`)
  } finally {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  }
}

function rawRequest(base: string, pathname: string, method: string, body: string) {
  return fetch(`${base}${pathname}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body,
  })
}

test('JSON として読めない本文やオブジェクトでない本文は 400 にする', async () => {
  await withServer(async (base) => {
    for (const body of ['{', 'null', '[]', '1', '"x"']) {
      const res = await rawRequest(base, '/api/speak', 'POST', body)
      assert.equal(res.status, 400, body)
      assert.equal((await readJson(res)).error, 'invalid_json', body)
    }
    for (const pathname of ['/api/speak', '/api/speaker-vector', '/api/mute']) {
      assert.equal((await rawRequest(base, pathname, 'POST', 'null')).status, 400, pathname)
    }
  })
})

test('pairvoice に繋がらなければ /api/mute と /api/speak は 502 で申告する', async () => {
  await withServer(async (base) => {
    for (const [pathname, body] of [
      ['/api/mute', { minutes: 5 }],
      ['/api/speak', { text: 'テスト' }],
    ] as const) {
      const res = await fetch(`${base}${pathname}`, jsonInit('POST', body))
      assert.equal(res.status, 502, pathname)
      const parsed = await readJson(res)
      assert.equal(parsed.error, 'pairvoice_unreachable')
      assert.equal(typeof parsed.message, 'string')
    }
  })
})

test('/api/speak はクライアントが切ったら pairvoice への要求も切る', async () => {
  let upstreamClosed!: () => void
  const closed = new Promise<void>((resolve) => {
    upstreamClosed = resolve
  })
  let received!: () => void
  const arrived = new Promise<void>((resolve) => {
    received = resolve
  })
  // 応答を返さず、切られるのを待つ
  const fake = await startFakeUpstream((req, res) => {
    res.on('close', () => upstreamClosed())
    received()
  })
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = fake.url
  try {
    await withServer(async (base) => {
      const ctrl = new AbortController()
      const pending = fetch(`${base}/api/speak`, {
        ...jsonInit('POST', { text: 'テスト' }),
        signal: ctrl.signal,
      }).catch(() => null)
      await arrived
      ctrl.abort()
      await pending
      await Promise.race([
        closed,
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('pairvoice への要求が切られない')), 2000),
        ),
      ])
    })
  } finally {
    process.env.PAIRVOICE_URL = prevUrl
    await fake.close()
  }
})

test('プロンプト・辞書・スタイル・声の読み書きは、パスから /api を外して pairvoice へ中継する', async () => {
  const seen: { method?: string; url?: string; type?: string; body: string }[] = []
  const upstream = await startFakeUpstream((req, res) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => {
      seen.push({
        method: req.method,
        url: req.url,
        type: req.headers['content-type'],
        body: Buffer.concat(chunks).toString('utf8'),
      })
      if (req.url?.endsWith('/audio')) {
        res.writeHead(200, { 'Content-Type': 'audio/wav' })
        res.end('RIFFwav')
        return
      }
      res.writeHead(req.url === '/profiles/p-x' ? 404 : 200, {
        'Content-Type': 'application/json',
      })
      res.end(JSON.stringify({ relayed: req.url }))
    })
  })
  const prevUrl = process.env.PAIRVOICE_URL
  process.env.PAIRVOICE_URL = upstream.url
  try {
    await withServer(async (base) => {
      const put = await fetch(`${base}/api/prompt`, jsonInit('PUT', { text: 'ルール' }))
      assert.equal(put.status, 200)
      assert.deepEqual(await readJson(put), { relayed: '/prompt' })

      const upload = await fetch(`${base}/api/profiles/upload?name=${encodeURIComponent('声')}`, {
        method: 'POST',
        headers: { 'Content-Type': 'audio/wav' },
        body: 'RIFF\0\0\0\0WAVE',
      })
      assert.equal(upload.status, 200)

      // pairvoice の 404 はそのまま返す
      const missing = await fetch(`${base}/api/profiles/p-x`, { method: 'DELETE' })
      assert.equal(missing.status, 404)

      // 音声は JSON に包まずに流す
      const audio = await fetch(`${base}/api/profiles/p-a/audio`)
      assert.equal(audio.headers.get('content-type'), 'audio/wav')
      assert.equal(await audio.text(), 'RIFFwav')
    })
    assert.deepEqual(
      seen.map(({ method, url, type, body }) => ({ method, url, type, body })),
      [
        {
          method: 'PUT',
          url: '/prompt',
          type: 'application/json',
          body: JSON.stringify({ text: 'ルール' }),
        },
        {
          method: 'POST',
          url: `/profiles/upload?name=${encodeURIComponent('声')}`,
          type: 'audio/wav',
          body: 'RIFF\0\0\0\0WAVE',
        },
        { method: 'DELETE', url: '/profiles/p-x', type: undefined, body: '' },
        { method: 'GET', url: '/profiles/p-a/audio', type: undefined, body: '' },
      ],
    )
  } finally {
    process.env.PAIRVOICE_URL = prevUrl
    await upstream.close()
  }
})

test('pairvoice に繋がらなければ、中継する読み書きも 502 で申告する', async () => {
  await withServer(async (base) => {
    for (const pathname of ['/api/prompt', '/api/dict', '/api/styles', '/api/profiles']) {
      const res = await fetch(`${base}${pathname}`)
      assert.equal(res.status, 502, pathname)
      assert.equal((await readJson(res)).error, 'pairvoice_unreachable', pathname)
    }
  })
})
