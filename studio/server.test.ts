import assert from 'node:assert/strict'
import fs from 'node:fs'
import { mkdtemp, readdir, readFile, rename, rm } from 'node:fs/promises'
import http from 'node:http'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'

import type { StudioHealth } from './shared/api-types.ts'

// データの置き場所はリポジトリ外にあるのが正しいので一時ディレクトリに向ける。
// studio 自身の成果物（history/ など）もこの下に入るので、テストが
// リポジトリを汚さない。
const tmpRoot = await mkdtemp(path.join(os.tmpdir(), 'studio-test-'))
process.env.PAIRVOICE_DATA_ROOT = tmpRoot
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

import { writeFile } from 'node:fs/promises'

test('GET /api/corpus merges reviews and reverses to newest-first', async () => {
  const { CORPUS_FILE, REVIEWS_FILE } = await import('./server.ts')
  await writeFile(
    CORPUS_FILE,
    [
      JSON.stringify({
        ts: '2026-01-01 00:00:00',
        message_id: 'm1',
        input: 'in1',
        summary: 'out1',
        audio_path: '',
      }),
      JSON.stringify({
        ts: '2026-01-01 00:01:00',
        message_id: 'm2',
        input: 'in2',
        summary: 'out2',
        audio_path: 'generations/x.wav',
      }),
    ].join('\n') + '\n',
    'utf8',
  )
  await writeFile(
    REVIEWS_FILE,
    JSON.stringify({
      ts: '2026-01-01 00:02:00',
      message_id: 'm2',
      verdict: 'bad',
      ideal: 'こう言ってほしかった',
    }) + '\n',
    'utf8',
  )

  const server = await startServer(0)
  const port = portOf(server)
  const res = await fetch(`http://127.0.0.1:${port}/api/corpus`)
  const body = await readJson(res)
  assert.equal(body.total, 2)
  assert.equal(body.items[0].message_id, 'm2') // 新しい順
  assert.equal(body.items[0].verdict, 'bad')
  assert.equal(body.items[0].ideal, 'こう言ってほしかった')
  assert.equal(body.items[1].verdict, null)
  await new Promise((resolve) => server.close(resolve))
})

test('POST /api/reviews with verdict "none" resets a review to unreviewed in /api/corpus', async () => {
  const { CORPUS_FILE, REVIEWS_FILE } = await import('./server.ts')
  await writeFile(
    CORPUS_FILE,
    JSON.stringify({
      ts: '2026-01-01 00:00:00',
      message_id: 'mnone',
      input: 'in',
      summary: 'out',
      audio_path: '',
    }) + '\n',
    'utf8',
  )
  await writeFile(
    REVIEWS_FILE,
    JSON.stringify({
      ts: '2026-01-01 00:01:00',
      message_id: 'mnone',
      verdict: 'bad',
      ideal: '理想',
    }) + '\n',
    'utf8',
  )

  const server = await startServer(0)
  const port = portOf(server)

  const noneRes = await fetch(`http://127.0.0.1:${port}/api/reviews`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: 'mnone', verdict: 'none' }),
  })
  assert.equal(noneRes.status, 200)

  const corpusRes = await fetch(`http://127.0.0.1:${port}/api/corpus`)
  const body = await readJson(corpusRes)
  const item = body.items.find((c: { message_id: string }) => c.message_id === 'mnone')
  assert.equal(item.verdict, null)
  assert.equal(item.ideal, null)

  const raw = await (await import('node:fs/promises')).readFile(REVIEWS_FILE, 'utf8')
  const lines = raw.trim().split('\n')
  assert.equal(lines.length, 2) // 追記専用: 元のbadレコードは残り、noneが追記される
  assert.ok(JSON.parse(lines[1]!).verdict === 'none') // 直前に2行あることを確かめている

  await new Promise((resolve) => server.close(resolve))
})

test('POST /api/reviews appends a record and rejects invalid verdict', async () => {
  const { REVIEWS_FILE } = await import('./server.ts')
  const server = await startServer(0)
  const port = portOf(server)

  const bad = await fetch(`http://127.0.0.1:${port}/api/reviews`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: 'm3', verdict: 'maybe' }),
  })
  assert.equal(bad.status, 400)

  const ok = await fetch(`http://127.0.0.1:${port}/api/reviews`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: 'm3', verdict: 'good' }),
  })
  assert.equal(ok.status, 200)

  const raw = await (await import('node:fs/promises')).readFile(REVIEWS_FILE, 'utf8')
  assert.ok(raw.includes('"message_id":"m3"'))
  await new Promise((resolve) => server.close(resolve))
})

test('POST /api/archives appends a record, rejects invalid body, and GET /api/corpus reflects it', async () => {
  const { CORPUS_FILE, ARCHIVES_FILE } = await import('./server.ts')
  await writeFile(
    CORPUS_FILE,
    JSON.stringify({ ts: 't', message_id: 'arc1', input: 'in', summary: 'out', audio_path: '' }) +
      '\n',
    'utf8',
  )

  const server = await startServer(0)
  const port = portOf(server)

  const bad = await fetch(`http://127.0.0.1:${port}/api/archives`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: '', archived: true }),
  })
  assert.equal(bad.status, 400)

  const bad2 = await fetch(`http://127.0.0.1:${port}/api/archives`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: 'arc1', archived: 'yes' }),
  })
  assert.equal(bad2.status, 400)

  const ok = await fetch(`http://127.0.0.1:${port}/api/archives`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: 'arc1', archived: true }),
  })
  assert.equal(ok.status, 200)

  let corpus = await readJson(await fetch(`http://127.0.0.1:${port}/api/corpus`))
  assert.equal(
    corpus.items.find((c: { message_id: string }) => c.message_id === 'arc1').archived,
    true,
  )

  // 解除も追記で表現できる
  await fetch(`http://127.0.0.1:${port}/api/archives`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_id: 'arc1', archived: false }),
  })
  corpus = await readJson(await fetch(`http://127.0.0.1:${port}/api/corpus`))
  assert.equal(
    corpus.items.find((c: { message_id: string }) => c.message_id === 'arc1').archived,
    false,
  )

  const raw = await (await import('node:fs/promises')).readFile(ARCHIVES_FILE, 'utf8')
  assert.equal(raw.trim().split('\n').length, 2) // 追記専用

  await new Promise((resolve) => server.close(resolve))
})

test('POST /api/archives/bulk archives multiple message_ids in one request, rejects empty array', async () => {
  const { CORPUS_FILE } = await import('./server.ts')
  await writeFile(
    CORPUS_FILE,
    [
      JSON.stringify({ ts: 't', message_id: 'bulk1', input: 'in', summary: 'out', audio_path: '' }),
      JSON.stringify({ ts: 't', message_id: 'bulk2', input: 'in', summary: 'out', audio_path: '' }),
    ].join('\n') + '\n',
    'utf8',
  )

  const server = await startServer(0)
  const port = portOf(server)

  const bad = await fetch(`http://127.0.0.1:${port}/api/archives/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_ids: [], archived: true }),
  })
  assert.equal(bad.status, 400)

  const ok = await fetch(`http://127.0.0.1:${port}/api/archives/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_ids: ['bulk1', 'bulk2'], archived: true }),
  })
  assert.equal(ok.status, 200)

  const corpus = await readJson(await fetch(`http://127.0.0.1:${port}/api/corpus`))
  assert.equal(
    corpus.items.find((c: { message_id: string }) => c.message_id === 'bulk1').archived,
    true,
  )
  assert.equal(
    corpus.items.find((c: { message_id: string }) => c.message_id === 'bulk2').archived,
    true,
  )

  await new Promise((resolve) => server.close(resolve))
})

import { mkdir } from 'node:fs/promises'

test('GET /api/audio/:message_id streams the wav file', async () => {
  const { CORPUS_FILE, DATA_ROOT } = await import('./server.ts')
  await mkdir(path.join(DATA_ROOT, 'generations'), { recursive: true })
  await writeFile(path.join(DATA_ROOT, 'generations', 'a1.wav'), Buffer.from('RIFF-fake-wav-body'))
  await writeFile(
    CORPUS_FILE,
    JSON.stringify({
      ts: 't',
      message_id: 'aud1',
      input: 'in',
      summary: 'out',
      audio_path: 'generations/a1.wav',
    }) + '\n',
    'utf8',
  )

  const server = await startServer(0)
  const port = portOf(server)
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/audio/aud1`)
    assert.equal(res.status, 200)
    assert.equal(res.headers.get('content-type'), 'audio/wav')
    assert.equal(Buffer.from(await res.arrayBuffer()).toString(), 'RIFF-fake-wav-body')
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
})

test('GET /api/audio/:message_id rejects path traversal in audio_path', async () => {
  const { CORPUS_FILE } = await import('./server.ts')
  await writeFile(
    CORPUS_FILE,
    JSON.stringify({
      ts: 't',
      message_id: 'evil1',
      input: 'in',
      summary: 'out',
      audio_path: '../../../etc/passwd',
    }) + '\n',
    'utf8',
  )

  const server = await startServer(0)
  const port = portOf(server)
  const res = await fetch(`http://127.0.0.1:${port}/api/audio/evil1`)
  // パス脱出の検証は resolveAudioPath 側に移った（見つからない場合と区別しない）ため 404 になる。
  assert.equal(res.status, 404)
  await new Promise((resolve) => server.close(resolve))
})

test('GET /api/audio/:message_id returns 404 for unknown message_id', async () => {
  const server = await startServer(0)
  const port = portOf(server)
  const res = await fetch(`http://127.0.0.1:${port}/api/audio/does-not-exist`)
  assert.equal(res.status, 404)
  await new Promise((resolve) => server.close(resolve))
})

test('makeAudioPathResolver は置き場所の下にある実ファイルだけを返し、無ければ null を返す', async () => {
  const { makeAudioPathResolver } = await import('./server.ts')
  const root = await mkdtemp(path.join(os.tmpdir(), 'pairvoice-test-'))
  await mkdir(path.join(root, 'generations'), { recursive: true })
  await writeFile(path.join(root, 'generations', 'a.wav'), 'a')

  const resolve = makeAudioPathResolver(root)

  assert.equal(await resolve('generations/a.wav'), path.join(root, 'generations', 'a.wav'))
  assert.equal(await resolve('generations/missing.wav'), null)
})

test('makeAudioPathResolver はディレクトリの外を指すパスを拒否する', async () => {
  const { makeAudioPathResolver } = await import('./server.ts')
  const root = await mkdtemp(path.join(os.tmpdir(), 'pairvoice-test-'))
  const resolve = makeAudioPathResolver(root)

  assert.equal(await resolve('../../etc/passwd'), null)
  assert.equal(await resolve('/etc/passwd'), null)
})

import { execFileSync } from 'node:child_process'

// 辞書の置換は Python（常駐サーバーが合成の直前にかける）と JS（studio のプレビュー）に
// 二重実装されている。食い違うと studio のプレビューが嘘をつくので、記号・CRLF を含む辞書で
// 両者が同じ結果になるかを見る
test('Python の apply_dict と JS の applyDict は記号や CRLF を含む辞書で一致する', async () => {
  const { applyDict } = await import('./server.ts')
  const dir = await mkdtemp(path.join(os.tmpdir(), 'pairvoice-dict-'))
  try {
    const lf = 'A\tエー\tメモ\n&\tアンド\n#\tシャープ\nq\t"引用"\nb\t\\&\nエー\tえー\n'
    // Windows のエディタで保存されると CRLF になる。行末の \r を置換先に混ぜない
    const crlf = lf.replaceAll('\n', '\r\n')
    const input = 'A & # q b A'
    for (const tsv of [lf, crlf]) {
      const dictPath = path.join(dir, 'dict.tsv')
      await writeFile(dictPath, tsv)
      // 合成は load_dict で読んで置き換え、studio のプレビューは GET /dict（read_rows）の行で置き換える
      const out = execFileSync(
        'uv',
        [
          'run',
          '--quiet',
          'python',
          '-c',
          'import json, sys; from pathlib import Path; ' +
            'from pairvoice.reading import apply_dict, load_dict, read_rows; ' +
            'p = Path(sys.argv[1]); ' +
            'json.dump({"applied": apply_dict(sys.argv[2], load_dict(p)), "rows": read_rows(p)}, sys.stdout)',
          dictPath,
          input,
        ],
        { cwd: path.join(import.meta.dirname, '..'), encoding: 'utf8' },
      )
      const { applied, rows } = JSON.parse(out)
      assert.equal(applied, applyDict(input, rows), tsv === crlf ? 'CRLF' : 'LF')
    }
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
})
test('POST /api/dict/test applies rows in order without saving', async () => {
  const server = await startServer(0)
  const port = portOf(server)
  const res = await fetch(`http://127.0.0.1:${port}/api/dict/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text: 'PR #4688 が通った',
      rows: [
        { from: '通っ', to: 'とおっ', memo: '' },
        { from: '#', to: '', memo: '' },
      ],
    }),
  })
  const body = await readJson(res)
  assert.equal(body.result, 'PR 4688 がとおった')
  await new Promise((resolve) => server.close(resolve))
})

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

test('データの置き場所はコードの外にあり、静的ファイルはコードの置き場所から配信する', async () => {
  const { DATA_ROOT, DIST_DIR } = await import('./server.ts')
  // テストは PAIRVOICE_DATA_ROOT を一時ディレクトリに向けてあるので、コードの置き場所の外になる
  assert.ok(!DATA_ROOT.startsWith(path.dirname(STUDIO_DIR) + path.sep))
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

test('parseHistoryTs / parseCorpusTs read the two timestamp formats on the same number line', async () => {
  const { parseHistoryTs, parseCorpusTs } = await import('./server.ts')
  // snapshotPrompt が付ける名前（ISO の : と . を - に潰したもの）を時刻に戻せること
  assert.equal(
    parseHistoryTs('prompt-2026-07-28T05-03-13-373Z.txt'),
    Date.parse('2026-07-28T05:03:13.373Z'),
  )
  assert.equal(parseHistoryTs('prompt-latest.txt'), null)
  assert.equal(parseHistoryTs('notes.txt'), null)
  // corpus.jsonl の ts はオフセットの無いローカル時刻。同じ壁時計を UTC として
  // 読んでしまうと時差の分だけ境界がずれ、差し替え直後の数件を取り違える。
  assert.equal(parseCorpusTs('2026-07-28 14:01:59'), new Date(2026, 6, 28, 14, 1, 59).getTime())
  assert.equal(parseCorpusTs('t'), null)
  assert.equal(parseCorpusTs(undefined), null)
})

// 境界を指定の時刻に固定する。名前が一番新しいスナップショットの中身を prompt.txt と
// 一致させることで、そのスナップショットが「いまの版が動き出した時刻」になる。
// .test-state/history には他のテストが作ったスナップショットが残るので、未来の日付で
// 置いて最新の位置を握らないと結果が実行順に左右される。
// 残すと /api/prompt/history のテストを壊すので必ず消す。
async function withPromptSnapshotAt(name: string, fn: () => Promise<void>) {
  const { HISTORY_DIR, PROMPT_FILE } = await import('./server.ts')
  const file = path.join(HISTORY_DIR, name)
  await fs.promises.mkdir(HISTORY_DIR, { recursive: true })
  await writeFile(file, 'boundary', 'utf8')
  await writeFile(PROMPT_FILE, 'boundary', 'utf8')
  try {
    await fn()
  } finally {
    await rm(file, { force: true })
  }
}

test('現行プロンプトの開始時刻は中身で決まる（同じ内容の保存し直しでは動かない）', async () => {
  const { HISTORY_DIR, PROMPT_FILE, currentPromptSince } = await import('./server.ts')
  await fs.promises.mkdir(HISTORY_DIR, { recursive: true })
  const before = new Set(await readdir(HISTORY_DIR))
  // pairvoice（prompt.py）と同じく、prompt.txt を書いた後に書いた内容で版を撮る
  let at = Date.parse('2999-01-01T00:00:00.000Z')
  const save = async (text: string) => {
    await writeFile(PROMPT_FILE, text, 'utf8')
    const name = `prompt-${new Date(at).toISOString().replace(/[:.]/g, '-')}.txt`
    await writeFile(path.join(HISTORY_DIR, name), text, 'utf8')
    at += 1000
  }
  try {
    await save('ひとつ前の版')
    await save('いまの版')
    const since = await currentPromptSince()
    assert.ok(since !== null, '境界が引けていない')

    // 中身を変えずに保存し直しても境界は動かない。動くと、何も変えていないのに
    // 直前までの読み上げが全部「旧プロンプト」に落ちてレビュー待ちが消える。
    await save('いまの版')
    assert.equal(await currentPromptSince(), since)

    // 中身が変われば境界は進む
    await save('次の版')
    assert.ok((await currentPromptSince())! > since) // 直前に書いた版が履歴の最新

    // API を通さず prompt.txt を書き換えると、いつからの版か分からないので境界を引かない
    await writeFile(PROMPT_FILE, '履歴に無い版', 'utf8')
    assert.equal(await currentPromptSince(), null)
  } finally {
    // 未来の日付の版を残すと、ほかのテストの境界を握ってしまう
    for (const name of await readdir(HISTORY_DIR)) {
      if (!before.has(name)) await rm(path.join(HISTORY_DIR, name), { force: true })
    }
  }
})
test('GET /api/corpus marks readings made before the current prompt as stale', async () => {
  const { CORPUS_FILE } = await import('./server.ts')
  await writeFile(
    CORPUS_FILE,
    [
      JSON.stringify({
        ts: '2099-01-01 00:00:00',
        message_id: 'old',
        input: 'in',
        summary: 'out',
        audio_path: '',
      }),
      JSON.stringify({
        ts: '2099-12-31 00:00:00',
        message_id: 'new',
        input: 'in',
        summary: 'out',
        audio_path: '',
      }),
      // 時刻が読めない行は「古い」と言い切る根拠が無いので古い側に落とさない
      JSON.stringify({
        ts: 'こわれた',
        message_id: 'unknown',
        input: 'in',
        summary: 'out',
        audio_path: '',
      }),
    ].join('\n') + '\n',
    'utf8',
  )

  await withPromptSnapshotAt('prompt-2099-06-01T00-00-00-000Z.txt', async () => {
    const server = await startServer(0)
    const port = portOf(server)
    try {
      const body = await readJson(await fetch(`http://127.0.0.1:${port}/api/corpus`))
      const byId = new Map<string, any>(
        body.items.map((i: { message_id: string }) => [i.message_id, i]),
      )
      // 3件とも直前に書いた corpus.jsonl にあるので必ず引ける
      assert.equal(byId.get('old')!.stale, true)
      assert.equal(byId.get('new')!.stale, false)
      assert.equal(byId.get('unknown')!.stale, false)
      assert.equal(body.prompt_changed_at, '2099-06-01T00:00:00.000Z')
    } finally {
      await new Promise((resolve) => server.close(resolve))
    }
  })
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

test('GET /api/audio-file は生成された wav を返し、データの置き場所の外を拒否する', async () => {
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const dir = path.join(tmpRoot, 'generations')
    await mkdir(dir, { recursive: true })
    await writeFile(path.join(dir, 'preview.wav'), 'RIFFfake', 'utf8')

    const ok = await fetch(`http://127.0.0.1:${port}/api/audio-file?path=generations/preview.wav`)
    assert.equal(ok.status, 200)
    assert.equal(ok.headers.get('content-type'), 'audio/wav')
    assert.equal(await ok.text(), 'RIFFfake')

    const escaped = await fetch(`http://127.0.0.1:${port}/api/audio-file?path=../../etc/passwd`)
    assert.equal(escaped.status, 404)

    const missing = await fetch(`http://127.0.0.1:${port}/api/audio-file?path=generations/nope.wav`)
    assert.equal(missing.status, 404)

    const empty = await fetch(`http://127.0.0.1:${port}/api/audio-file`)
    assert.equal(empty.status, 400)
  } finally {
    await new Promise((resolve) => server.close(resolve))
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
      assert.equal(await requestWithHeaders(port, '/api/corpus', { host }), 200, host)
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
      await requestWithHeaders(port, '/api/corpus', { host, origin: 'http://localhost:17493' }),
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

test('開けない音声は 500 で返し、プロセスを落とさない', async () => {
  const dir = path.join(tmpRoot, 'generations')
  await mkdir(dir, { recursive: true })
  const locked = path.join(dir, 'locked.wav')
  await writeFile(locked, 'RIFFfake')
  await fs.promises.chmod(locked, 0o000)
  try {
    await withServer(async (base) => {
      const res = await fetch(`${base}/api/audio-file?path=generations/locked.wav`)
      assert.equal(res.status, 500)
      assert.equal((await fetch(`${base}/api/health`)).status, 200)
    })
  } finally {
    await fs.promises.chmod(locked, 0o644)
    await rm(locked, { force: true })
  }
})

test('JSON として読めない本文やオブジェクトでない本文は 400 にする', async () => {
  await withServer(async (base) => {
    for (const body of ['{', 'null', '[]', '1', '"x"']) {
      const res = await rawRequest(base, '/api/reviews', 'POST', body)
      assert.equal(res.status, 400, body)
      assert.equal((await readJson(res)).error, 'invalid_json', body)
    }
    for (const pathname of ['/api/reviews', '/api/archives', '/api/speak', '/api/mute']) {
      assert.equal((await rawRequest(base, pathname, 'POST', 'null')).status, 400, pathname)
    }
  })
})

test('JSONL の末尾が改行で終わっていなければ、新しい行を壊れた行から切り離す', async () => {
  const { REVIEWS_FILE } = await import('./server.ts')
  await writeFile(REVIEWS_FILE, '{"message_id":"cut","verd')
  await withServer(async (base) => {
    const res = await fetch(
      `${base}/api/reviews`,
      jsonInit('POST', { message_id: 'after-cut', verdict: 'good' }),
    )
    assert.equal(res.status, 200)
  })
  const lines = (await readFile(REVIEWS_FILE, 'utf8')).split('\n')
  assert.equal(lines[0], '{"message_id":"cut","verd')
  assert.equal(JSON.parse(lines[1]!).message_id, 'after-cut')
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

test('POST /api/reviews は message_id と ideal の型を確かめ、GET /api/corpus は負の offset/limit を丸める', async () => {
  const { CORPUS_FILE } = await import('./server.ts')
  await writeFile(
    CORPUS_FILE,
    ['c1', 'c2', 'c3']
      .map((id) => JSON.stringify({ ts: 't', message_id: id, input: 'i', summary: 's' }))
      .join('\n') + '\n',
  )
  await withServer(async (base) => {
    for (const body of [
      { message_id: 1, verdict: 'good' },
      { message_id: ['x'], verdict: 'good' },
      { message_id: 'm', verdict: 'bad', ideal: 5 },
      { message_id: 'm', verdict: 'bad', ideal: { a: 1 } },
    ]) {
      const res = await fetch(`${base}/api/reviews`, jsonInit('POST', body))
      assert.equal(res.status, 400, JSON.stringify(body))
    }
    const ids = async (query: string) =>
      (await readJson(await fetch(`${base}/api/corpus?${query}`))).items.map(
        (c: { message_id: string }) => c.message_id,
      )
    assert.deepEqual(await ids('offset=-1&limit=2'), ['c3', 'c2'])
    assert.deepEqual(await ids('offset=0&limit=-1'), [])
    assert.deepEqual(await ids('offset=1.5&limit=1'), ['c2'])
  })
})

test('GET /api/audio-file は生成音声と参照音声だけを配り、symlink で外へ出させない', async () => {
  const { DATA_ROOT } = await import('./server.ts')
  const profiles = path.join(DATA_ROOT, 'profiles')
  await mkdir(path.join(profiles, 'p-a'), { recursive: true })
  await writeFile(path.join(profiles, 'p-a', 'reference.wav'), 'RIFF\0\0\0\0WAVE')
  await writeFile(path.join(profiles, 'p-a', 'profile.json'), '{}')
  const gen = path.join(DATA_ROOT, 'generations')
  await mkdir(gen, { recursive: true })
  const outside = await mkdtemp(path.join(os.tmpdir(), 'studio-outside-'))
  await writeFile(path.join(outside, 'secret.wav'), 'secret')
  const link = path.join(gen, 'link.wav')
  await rm(link, { force: true })
  await fs.promises.symlink(path.join(outside, 'secret.wav'), link)
  const inner = path.join(gen, 'inner.wav')
  await rm(inner, { force: true })
  await fs.promises.symlink(path.join(DATA_ROOT, 'profiles', 'p-a', 'profile.json'), inner)
  await writeFile(path.join(gen, 'note.txt'), 'note')
  await mkdir(path.join(DATA_ROOT, 'custom-out'), { recursive: true })
  await writeFile(path.join(DATA_ROOT, 'custom-out', 'take.wav'), 'RIFF')
  try {
    await withServer(async (base) => {
      const get = (p: string) => fetch(`${base}/api/audio-file?path=${encodeURIComponent(p)}`)
      assert.equal((await get('profiles/p-a/reference.wav')).status, 200)
      // tts.output_dir の名前は設定しだい。データの置き場所の下の wav なら配る
      assert.equal((await get('custom-out/take.wav')).status, 200)
      for (const p of [
        'generations/note.txt',
        'profiles/p-a/profile.json',
        'dict.tsv',
        'reviews.jsonl',
        'generations/link.wav',
        'generations/inner.wav',
      ]) {
        const res = await get(p)
        assert.equal(res.status, 404, p)
        await res.arrayBuffer()
      }
    })
  } finally {
    await rm(link, { force: true })
    await rm(inner, { force: true })
    await rm(outside, { recursive: true, force: true })
    await rm(profiles, { recursive: true, force: true })
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
