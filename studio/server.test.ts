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
  const { parseDictTsv, applyDict } = await import('./server.ts')
  const dir = await mkdtemp(path.join(os.tmpdir(), 'pairvoice-dict-'))
  try {
    const lf = 'A\tエー\tメモ\n&\tアンド\n#\tシャープ\nq\t"引用"\nb\t\\&\nエー\tえー\n'
    // Windows のエディタで保存されると CRLF になる。行末の \r を置換先に混ぜない
    const crlf = lf.replaceAll('\n', '\r\n')
    assert.deepEqual(parseDictTsv(crlf), parseDictTsv(lf))
    const input = 'A & # q b A'
    for (const tsv of [lf, crlf]) {
      const dictPath = path.join(dir, 'dict.tsv')
      await writeFile(dictPath, tsv)
      const out = execFileSync(
        'uv',
        [
          'run',
          '--quiet',
          'python',
          '-c',
          'import sys; from pathlib import Path; from pairvoice.reading import apply_dict, load_dict; ' +
            'sys.stdout.write(apply_dict(sys.argv[2], load_dict(Path(sys.argv[1]))))',
          dictPath,
          input,
        ],
        { cwd: path.join(import.meta.dirname, '..'), encoding: 'utf8' },
      )
      assert.equal(out, applyDict(input, parseDictTsv(lf)), tsv === crlf ? 'CRLF' : 'LF')
    }
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
})

test('GET/PUT /api/dict round-trips rows, rejects empty "from"', async () => {
  const { DICT_FILE } = await import('./server.ts')
  const server = await startServer(0)
  const port = portOf(server)

  const putBad = await fetch(`http://127.0.0.1:${port}/api/dict`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows: [{ from: '', to: 'x', memo: '' }] }),
  })
  assert.equal(putBad.status, 400)

  const rows = [
    { from: '通っ', to: 'とおっ', memo: '' },
    { from: '#', to: '', memo: 'PR番号のハッシュを除去' },
  ]
  const putOk = await fetch(`http://127.0.0.1:${port}/api/dict`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows }),
  })
  assert.equal(putOk.status, 200)

  const getRes = await fetch(`http://127.0.0.1:${port}/api/dict`)
  const body = await readJson(getRes)
  assert.deepEqual(body.rows, rows)

  const raw = await (await import('node:fs/promises')).readFile(DICT_FILE, 'utf8')
  assert.equal(raw, '通っ\tとおっ\t\n#\t\tPR番号のハッシュを除去\n')
  await new Promise((resolve) => server.close(resolve))
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

test('PUT /api/prompt は保存した内容を履歴の最新として残し、GET に反映する', async () => {
  const { PROMPT_FILE, HISTORY_DIR } = await import('./server.ts')
  const fsp = await import('node:fs/promises')
  // 履歴の件数と最新の中身を見るので、このテストは履歴を空にしてから始める
  await fsp.rm(HISTORY_DIR, { recursive: true, force: true })
  await fsp.mkdir(HISTORY_DIR, { recursive: true })
  await writeFile(PROMPT_FILE, '旧プロンプト', 'utf8')

  const server = await startServer(0)
  const port = portOf(server)

  const putRes = await fetch(`http://127.0.0.1:${port}/api/prompt`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: '新プロンプト' }),
  })
  assert.equal(putRes.status, 200)

  const getRes = await fetch(`http://127.0.0.1:${port}/api/prompt`)
  assert.equal((await readJson(getRes)).text, '新プロンプト')

  // 「かつて動いていた版はすべて履歴にある」ので、いま動いている版が履歴の最新になる
  const historyFiles = (await fsp.readdir(HISTORY_DIR)).toSorted()
  assert.equal(historyFiles.length, 1)
  const snapshotContent = await fsp.readFile(path.join(HISTORY_DIR, historyFiles.at(-1)!), 'utf8')
  assert.equal(snapshotContent, '新プロンプト')

  const emptyRes = await fetch(`http://127.0.0.1:${port}/api/prompt`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: '' }),
  })
  assert.equal(emptyRes.status, 400)

  await new Promise((resolve) => server.close(resolve))
})

test('POST /api/prompt/restore restores a snapshot and rejects path traversal names', async () => {
  const { PROMPT_FILE, HISTORY_DIR } = await import('./server.ts')
  // 前のテストが作ったスナップショットが累積するため、このテストは履歴を空にしてから始める
  const fsp = await import('node:fs/promises')
  await fsp.rm(HISTORY_DIR, { recursive: true, force: true })
  await fsp.mkdir(HISTORY_DIR, { recursive: true })
  await writeFile(PROMPT_FILE, '初期プロンプト', 'utf8')
  const server = await startServer(0)
  const port = portOf(server)

  const put = (text: string) =>
    fetch(`http://127.0.0.1:${port}/api/prompt`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
  await put('版A')
  // スナップショットのファイル名はミリ秒精度の時刻なので、保存の間隔を空けて衝突を避ける
  await new Promise((r) => setTimeout(r, 5))
  await put('版B')

  const historyRes = await fetch(`http://127.0.0.1:${port}/api/prompt/history`)
  const { items } = await readJson(historyRes)
  assert.equal(items.length, 2) // 保存した2版がどちらも履歴にある（新しい順）

  const restoreRes = await fetch(`http://127.0.0.1:${port}/api/prompt/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: items.at(-1).name }), // 古い方＝版A
  })
  assert.equal(restoreRes.status, 200)

  const afterRes = await fetch(`http://127.0.0.1:${port}/api/prompt`)
  assert.equal((await readJson(afterRes)).text, '版A')

  // 復元も「その版が再び動き出した」記録として履歴に1件増え、最新が現行版と一致する
  const after = await readJson(await fetch(`http://127.0.0.1:${port}/api/prompt/history`))
  assert.equal(after.items.length, 3)
  assert.equal(await fsp.readFile(path.join(HISTORY_DIR, after.items[0].name), 'utf8'), '版A')

  const evilRes = await fetch(`http://127.0.0.1:${port}/api/prompt/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: '../../../etc/passwd' }),
  })
  assert.equal(evilRes.status, 400)

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

test('プロンプトはデータの置き場所から読み、静的ファイルはコードの置き場所から配信する', async () => {
  const { DATA_ROOT, DIST_DIR } = await import('./server.ts')
  // テストは PAIRVOICE_DATA_ROOT を一時ディレクトリに向けてあるので、コードの置き場所の外になる
  assert.ok(!DATA_ROOT.startsWith(path.dirname(STUDIO_DIR) + path.sep))
  assert.equal(DIST_DIR, path.join(STUDIO_DIR, 'web', 'dist'))

  await writeFile(path.join(DATA_ROOT, 'prompt.txt'), 'データ側のプロンプト', 'utf8')

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
        const prompt = await readJson(await fetch(`${base}/api/prompt`))
        assert.equal(prompt.text, 'データ側のプロンプト')

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

test('データの置き場所がまだ存在しなくても書き込みが通る', async () => {
  // server.ts は読み込み時に定数を確定するので DATA_ROOT を差し替えられない。
  // 実際に効くのは atomicWrite が親ディレクトリを作るかどうかなので、そこを直接固定する。
  const { atomicWrite } = await import('./server.ts')
  const target = path.join(tmpRoot, 'not-created-yet', 'pairvoice', 'prompt.txt')
  await atomicWrite(target, 'ようこそ')
  assert.equal(await readFile(target, 'utf8'), 'ようこそ')
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
  const { HISTORY_DIR, PROMPT_FILE, writePromptWithSnapshot, currentPromptSince } =
    await import('./server.ts')
  await fs.promises.mkdir(HISTORY_DIR, { recursive: true })
  const before = new Set(await readdir(HISTORY_DIR))
  try {
    await writePromptWithSnapshot('ひとつ前の版')
    await new Promise((r) => setTimeout(r, 5)) // スナップショット名は ms 単位なので衝突を避ける
    await writePromptWithSnapshot('いまの版')
    const since = await currentPromptSince()
    assert.ok(since !== null, '境界が引けていない')

    // 中身を変えずに保存し直しても境界は動かない。動くと、何も変えていないのに
    // 直前までの読み上げが全部「旧プロンプト」に落ちてレビュー待ちが消える。
    await new Promise((r) => setTimeout(r, 5))
    await writePromptWithSnapshot('いまの版')
    assert.equal(await currentPromptSince(), since)

    // 中身が変われば境界は進む
    await new Promise((r) => setTimeout(r, 5))
    await writePromptWithSnapshot('次の版')
    assert.ok((await currentPromptSince())! > since) // 直前に書いた版が履歴の最新

    // studio を通さず prompt.txt を書き換えると、いつからの版か分からないので境界を引かない
    await writeFile(PROMPT_FILE, '履歴に無い版', 'utf8')
    assert.equal(await currentPromptSince(), null)
  } finally {
    // 実時刻のスナップショットを残すと /api/prompt/history のテストの並び順を壊す
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

// プロファイルを直接置く。pairvoice（profiles.py）が書くのと同じ形
async function placeProfile(id: string, meta: Record<string, unknown>, { active = false } = {}) {
  const { PROFILES_DIR } = await import('./server.ts')
  const dir = path.join(PROFILES_DIR, id)
  await fs.promises.mkdir(dir, { recursive: true })
  await writeFile(path.join(dir, 'reference.wav'), 'RIFF\0\0\0\0WAVE')
  await writeFile(path.join(dir, 'profile.json'), JSON.stringify(meta))
  if (active) await writeFile(path.join(PROFILES_DIR, 'active'), `${id}\n`)
  return dir
}

async function clearProfiles() {
  const { PROFILES_DIR } = await import('./server.ts')
  await rm(PROFILES_DIR, { recursive: true, force: true })
}

async function readProfileJson(id: string) {
  const { PROFILES_DIR } = await import('./server.ts')
  return JSON.parse(await readFile(path.join(PROFILES_DIR, id, 'profile.json'), 'utf8'))
}

test('PATCH /api/profiles/:id の caption は使用中でなくても書け、そのプロファイルの履歴に版を残す', async () => {
  const { HISTORY_DIR, PROFILES_DIR } = await import('./server.ts')
  await clearProfiles()
  await placeProfile(
    'p-a',
    { name: 'a', caption: '使用中の声。', source: 'design' },
    { active: true },
  )
  await placeProfile('p-b', {
    name: 'b',
    caption: '前の声。',
    source: 'design',
    created_at: 'x',
  })
  await fs.promises.mkdir(HISTORY_DIR, { recursive: true })
  const promptsBefore = await readdir(HISTORY_DIR)
  const server = await startServer(0)
  try {
    const base = `http://127.0.0.1:${portOf(server)}/api/profiles`
    const res = await fetch(`${base}/p-b`, jsonInit('PATCH', { caption: ' 新しく採用した声。 ' }))
    assert.equal(res.status, 200)
    assert.equal((await readJson(res)).caption, '新しく採用した声。')
    const meta = await readProfileJson('p-b')
    assert.equal(meta.caption, '新しく採用した声。')
    // caption 以外は書き換えない
    assert.equal(meta.name, 'b')
    assert.equal(meta.created_at, 'x')
    assert.equal((await readProfileJson('p-a')).caption, '使用中の声。')

    // 版はそのプロファイルの中に残り、ほかのプロファイルとプロンプトの履歴には混ざらない
    const history = await readJson(await fetch(`${base}/p-b/caption/history`))
    assert.equal(history.items.length, 1)
    const saved = path.join(PROFILES_DIR, 'p-b', 'history', history.items[0].name)
    assert.equal(await readFile(saved, 'utf8'), '新しく採用した声。')
    assert.equal((await readJson(await fetch(`${base}/p-a/caption/history`))).items.length, 0)
    assert.deepEqual(await readdir(HISTORY_DIR), promptsBefore)
    assert.equal((await fetch(`${base}/p-missing/caption/history`)).status, 404)
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
  }
})

test('PATCH /api/profiles/:id は caption を空にでき、空の名前と、どちらも無い本文を拒否する', async () => {
  await clearProfiles()
  await placeProfile('p-a', { name: 'a', caption: '声。', source: 'design' }, { active: true })
  const server = await startServer(0)
  try {
    const url = `http://127.0.0.1:${portOf(server)}/api/profiles/p-a`
    assert.equal((await fetch(url, jsonInit('PATCH', { name: '' }))).status, 400)
    assert.equal((await fetch(url, jsonInit('PATCH', {}))).status, 400)
    assert.equal((await readProfileJson('p-a')).caption, '声。')

    // 空の caption は「caption なしで読む」の指定として保存する
    assert.equal((await fetch(url, jsonInit('PATCH', { caption: '   ' }))).status, 200)
    assert.equal((await readProfileJson('p-a')).caption, '')
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
  }
})

test('POST /api/profiles/:id/caption/restore はそのプロファイルの版を戻し、ほかの名前を拒否する', async () => {
  await clearProfiles()
  await placeProfile(
    'p-a',
    { name: 'a', caption: '最初の声。', source: 'design' },
    { active: true },
  )
  await placeProfile('p-b', { name: 'b', caption: 'B の声。', source: 'design' })
  const server = await startServer(0)
  try {
    const base = `http://127.0.0.1:${portOf(server)}/api/profiles`
    const put = (caption: string) => fetch(`${base}/p-a`, jsonInit('PATCH', { caption }))
    await put('ひとつ前の声。')
    await new Promise((r) => setTimeout(r, 5)) // 名前は ms 単位なので衝突を避ける
    await put('いまの声。')

    const history = await readJson(await fetch(`${base}/p-a/caption/history`))
    const oldest = history.items[history.items.length - 1].name
    const restore = (id: string, name: string) =>
      fetch(`${base}/${id}/caption/restore`, jsonInit('POST', { name }))
    assert.equal((await restore('p-a', oldest)).status, 200)
    assert.equal((await readProfileJson('p-a')).caption, 'ひとつ前の声。')

    // 復元も1件増える（その版が再び動き出した記録）
    const after = await readJson(await fetch(`${base}/p-a/caption/history`))
    assert.equal(after.items.length, history.items.length + 1)

    // ほかのプロファイルの版は当てられない
    assert.equal((await restore('p-b', oldest)).status, 404)
    assert.equal((await readProfileJson('p-b')).caption, 'B の声。')
    assert.equal((await restore('p-missing', oldest)).status, 404)
    assert.equal((await restore('p-a', '../../../etc/passwd')).status, 400)
    // プロンプトの版を caption として復元できてはいけない
    assert.equal((await restore('p-a', 'prompt-2099-01-01T00-00-00-000Z.txt')).status, 400)
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
  }
})

test('GET /api/profiles は新しい順に並べ、使用中の ID を添える', async () => {
  await clearProfiles()
  await placeProfile('p-old', {
    name: '古い',
    caption: '古い声。',
    source: 'auto',
    created_at: '2026-01-01T00:00:00Z',
  })
  await placeProfile(
    'p-new',
    { name: '新しい', caption: '新しい声。', source: 'design', created_at: '2026-02-01T00:00:00Z' },
    { active: true },
  )
  // 参照音声の無いものは載せない（pairvoice も読み飛ばす）
  const broken = await placeProfile('p-broken', { name: '壊れ', caption: '声。', source: 'upload' })
  await rm(path.join(broken, 'reference.wav'))
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const body = await readJson(await fetch(`http://127.0.0.1:${port}/api/profiles`))
    assert.deepEqual(
      body.items.map((item: { id: string }) => item.id),
      ['p-new', 'p-old'],
    )
    assert.equal(body.active, 'p-new')
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
  }
})

test('POST /api/profiles は試聴のテイクからプロファイルを作り、未設定なら使用中にする', async () => {
  const { DATA_ROOT, PROFILES_DIR } = await import('./server.ts')
  await clearProfiles()
  const take = path.join(DATA_ROOT, 'generations', 'take.wav')
  await fs.promises.mkdir(path.dirname(take), { recursive: true })
  await writeFile(take, pcmWav(10, [1, 2, 3]))
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const create = (name: string) =>
      fetch(`http://127.0.0.1:${port}/api/profiles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, caption: '作った声。', takes: ['generations/take.wav'] }),
      })
    const first = await create('一つ目')
    assert.equal(first.status, 201)
    const created = await readJson(first)
    assert.equal(created.source, 'design')
    assert.equal(created.caption, '作った声。')
    assert.deepEqual(
      await readFile(path.join(PROFILES_DIR, created.id, 'reference.wav')),
      pcmWav(10, [1, 2, 3]),
    )
    assert.equal(
      (await readJson(await fetch(`http://127.0.0.1:${port}/api/profiles`))).active,
      created.id,
    )

    // 2つ目は使用中を奪わない
    const second = await readJson(await create('二つ目'))
    const list = await readJson(await fetch(`http://127.0.0.1:${port}/api/profiles`))
    assert.equal(list.active, created.id)
    assert.ok(list.items.some((item: { id: string }) => item.id === second.id))
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
    await rm(take, { force: true })
  }
})

// モノラル・16bit の PCM wav
function pcmWav(sampleRate: number, samples: readonly number[]) {
  const data = Buffer.alloc(samples.length * 2)
  samples.forEach((v, i) => data.writeInt16LE(v, i * 2))
  const header = Buffer.alloc(44)
  header.write('RIFF', 0, 'latin1')
  header.writeUInt32LE(36 + data.length, 4)
  header.write('WAVEfmt ', 8, 'latin1')
  header.writeUInt32LE(16, 16)
  header.writeUInt16LE(1, 20)
  header.writeUInt16LE(1, 22)
  header.writeUInt32LE(sampleRate, 24)
  header.writeUInt32LE(sampleRate * 2, 28)
  header.writeUInt16LE(2, 32)
  header.writeUInt16LE(16, 34)
  header.write('data', 36, 'latin1')
  header.writeUInt32LE(data.length, 40)
  return Buffer.concat([header, data])
}

test('POST /api/profiles はテイクを複数渡すと、短い無音を挟んで1本の参照音声につなぐ', async () => {
  const { DATA_ROOT, PROFILES_DIR } = await import('./server.ts')
  await clearProfiles()
  const dir = path.join(DATA_ROOT, 'generations')
  await fs.promises.mkdir(dir, { recursive: true })
  await writeFile(path.join(dir, 'one.wav'), pcmWav(10, [1, 2]))
  await writeFile(path.join(dir, 'two.wav'), pcmWav(10, [3]))
  await writeFile(path.join(dir, 'other-rate.wav'), pcmWav(20, [4]))
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const create = (takes: string[]) =>
      fetch(`http://127.0.0.1:${port}/api/profiles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'つないだ声', caption: '', takes }),
      })
    const res = await create(['generations/one.wav', 'generations/two.wav'])
    assert.equal(res.status, 201)
    const created = await readJson(res)
    const joined = await readFile(path.join(PROFILES_DIR, created.id, 'reference.wav'))
    // 0.3 秒の無音（10Hz なら 3 サンプル）を挟む
    assert.deepEqual(joined, pcmWav(10, [1, 2, 0, 0, 0, 3]))

    // 形式の違う wav はつながない
    const mismatch = await create(['generations/one.wav', 'generations/other-rate.wav'])
    assert.equal(mismatch.status, 400)
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
    for (const name of ['one.wav', 'two.wav', 'other-rate.wav']) {
      await rm(path.join(dir, name), { force: true })
    }
  }
})

test('POST /api/profiles は参照音声やデータの置き場所の外の wav を取り込み元にさせない', async () => {
  await clearProfiles()
  await placeProfile('p-a', { name: 'a', caption: '声。', source: 'design' })
  const server = await startServer(0)
  try {
    const port = portOf(server)
    for (const take of [
      'profiles/p-a/reference.wav',
      '../outside.wav',
      'generations/missing.wav',
    ]) {
      const res = await fetch(`http://127.0.0.1:${port}/api/profiles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'x', caption: '声。', takes: [take] }),
      })
      assert.ok(res.status === 400 || res.status === 404, `${take}: ${res.status}`)
    }
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
  }
})

test('POST /api/profiles は wav 本体を取り込み、wav でなければ 400 を返す', async () => {
  const { PROFILES_DIR } = await import('./server.ts')
  await clearProfiles()
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const url = `http://127.0.0.1:${port}/api/profiles?name=${encodeURIComponent('手持ち')}&caption=${encodeURIComponent('低い声。')}`
    const ok = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/wav' },
      body: Buffer.from('RIFF\0\0\0\0WAVEdata'),
    })
    assert.equal(ok.status, 201)
    const created = await readJson(ok)
    assert.equal(created.name, '手持ち')
    assert.equal(created.source, 'upload')
    assert.equal(
      await readFile(path.join(PROFILES_DIR, created.id, 'reference.wav'), 'latin1'),
      'RIFF\0\0\0\0WAVEdata',
    )

    const mp3 = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/mpeg' },
      body: Buffer.from('ID3\x03\x00'),
    })
    assert.equal(mp3.status, 400)
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
  }
})

function jsonInit(method: string, body: unknown) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

test('プロファイルの切り替え・名前の変更・削除', async () => {
  const { PROFILES_DIR } = await import('./server.ts')
  await clearProfiles()
  await placeProfile('p-a', { name: 'a', caption: 'A の声。', source: 'design' }, { active: true })
  await placeProfile('p-b', { name: 'b', caption: 'B の声。', source: 'upload' })
  const server = await startServer(0)
  try {
    const port = portOf(server)
    const base = `http://127.0.0.1:${port}/api/profiles`

    assert.equal((await fetch(`${base}/active`, jsonInit('PUT', { id: 'p-missing' }))).status, 404)
    assert.equal((await fetch(`${base}/active`, jsonInit('PUT', { id: '../x' }))).status, 404)
    assert.equal((await fetch(`${base}/active`, jsonInit('PUT', { id: 'p-b' }))).status, 200)
    assert.equal(await readFile(path.join(PROFILES_DIR, 'active'), 'utf8'), 'p-b\n')

    const renamed = await fetch(`${base}/p-a`, jsonInit('PATCH', { name: '改名' }))
    assert.equal((await readJson(renamed)).name, '改名')
    assert.equal((await readProfileJson('p-a')).caption, 'A の声。')

    // 使用中は消せない
    assert.equal((await fetch(`${base}/p-b`, { method: 'DELETE' })).status, 409)
    assert.equal((await fetch(`${base}/p-a`, { method: 'DELETE' })).status, 200)
    assert.equal(fs.existsSync(path.join(PROFILES_DIR, 'p-a')), false)

    const audio = await fetch(`${base}/p-b/audio`)
    assert.equal(audio.status, 200)
    assert.equal(audio.headers.get('content-type'), 'audio/wav')
    assert.equal((await fetch(`${base}/p-a/audio`)).status, 404)
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await clearProfiles()
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
      assert.equal(await requestWithHeaders(port, '/api/dict', { host }), 200, host)
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
      await requestWithHeaders(port, '/api/dict', { host, origin: 'http://localhost:17493' }),
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

test('chunked で上限を超える wav は 413 で断る', async () => {
  await clearProfiles()
  await withServer(async (base) => {
    const { port } = new URL(base)
    const status = await new Promise<number>((resolve, reject) => {
      const req = http.request(
        {
          host: '127.0.0.1',
          port,
          method: 'POST',
          path: '/api/profiles?name=big',
          headers: { 'Content-Type': 'audio/wav', 'Transfer-Encoding': 'chunked' },
        },
        (res) => {
          res.resume()
          resolve(res.statusCode ?? 0)
        },
      )
      // 断られた後の書き込みは EPIPE / ECONNRESET になりうる。応答が来ていればそれでよい
      req.on('error', reject)
      const chunk = Buffer.alloc(1024 * 1024)
      let sent = 0
      const pump = () => {
        while (sent < 60) {
          sent++
          if (!req.write(chunk)) {
            req.once('drain', pump)
            return
          }
        }
        req.end()
      }
      pump()
    })
    assert.equal(status, 413)
    assert.equal((await readJson(await fetch(`${base}/api/profiles`))).items.length, 0)
  })
})

test('JSON として読めない本文やオブジェクトでない本文は 400 にする', async () => {
  await withServer(async (base) => {
    for (const body of ['{', 'null', '[]', '1', '"x"']) {
      const res = await rawRequest(base, '/api/dict', 'PUT', body)
      assert.equal(res.status, 400, body)
      assert.equal((await readJson(res)).error, 'invalid_json', body)
    }
    for (const pathname of ['/api/reviews', '/api/archives', '/api/speak', '/api/mute']) {
      assert.equal((await rawRequest(base, pathname, 'POST', 'null')).status, 400, pathname)
    }
  })
})

test('辞書の行がオブジェクトでなければ 400、タブや改行を含む値も 400 にする', async () => {
  const { DICT_FILE } = await import('./server.ts')
  await writeFile(DICT_FILE, 'a\tb\t\n')
  await withServer(async (base) => {
    for (const rows of [[null], [1], ['x']]) {
      assert.equal((await fetch(`${base}/api/dict`, jsonInit('PUT', { rows }))).status, 400)
      const tried = await fetch(`${base}/api/dict/test`, jsonInit('POST', { text: 'a', rows }))
      assert.equal(tried.status, 400)
    }
    for (const row of [
      { from: 'a\tb', to: 'x', memo: '' },
      { from: 'a', to: 'x\ny', memo: '' },
      { from: 'a', to: 'x', memo: 'm\r' },
    ]) {
      const res = await fetch(`${base}/api/dict`, jsonInit('PUT', { rows: [row] }))
      assert.equal(res.status, 400, JSON.stringify(row))
    }
  })
  assert.equal(await readFile(DICT_FILE, 'utf8'), 'a\tb\t\n')
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

test('同じプロファイルへの PATCH が重なっても、どちらの変更も残る', async () => {
  await clearProfiles()
  await placeProfile('p-a', { name: 'a', caption: '声。', source: 'design' }, { active: true })
  await withServer(async (base) => {
    for (let i = 0; i < 5; i++) {
      const [r1, r2] = await Promise.all([
        fetch(`${base}/api/profiles/p-a`, jsonInit('PATCH', { name: `名前${i}` })),
        fetch(`${base}/api/profiles/p-a`, jsonInit('PATCH', { caption: `声${i}。` })),
      ])
      assert.equal(r1.status, 200)
      assert.equal(r2.status, 200)
      const meta = await readProfileJson('p-a')
      assert.equal(meta.name, `名前${i}`)
      assert.equal(meta.caption, `声${i}。`)
    }
  })
  await clearProfiles()
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
  await clearProfiles()
  await placeProfile('p-a', { name: 'a', caption: '', source: 'design' })
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
    await clearProfiles()
  }
})

test('CRLF の dict.tsv を読んでも行末に \\r を残さず、そのまま保存し直せる', async () => {
  const { DICT_FILE } = await import('./server.ts')
  await writeFile(DICT_FILE, 'a\tb\tm\r\nc\td\r\n')
  try {
    await withServer(async (base) => {
      const { rows } = await readJson(await fetch(`${base}/api/dict`))
      assert.deepEqual(rows, [
        { from: 'a', to: 'b', memo: 'm' },
        { from: 'c', to: 'd', memo: '' },
      ])
      assert.equal((await fetch(`${base}/api/dict`, jsonInit('PUT', { rows }))).status, 200)
    })
  } finally {
    await rm(DICT_FILE, { force: true })
  }
})

test('プロファイルの削除は、同時に走る PATCH にディレクトリを作り直させない', async () => {
  const { PROFILES_DIR } = await import('./server.ts')
  await clearProfiles()
  await withServer(async (base) => {
    for (let i = 0; i < 30; i++) {
      const id = `p-race${i}`
      await placeProfile(id, { name: 'n', caption: '', source: 'design' })
      // PATCH が読み終えてから書くまでの間に DELETE が消すように、PATCH を先に出す
      const patch = fetch(`${base}/api/profiles/${id}`, jsonInit('PATCH', { caption: `声${i}。` }))
      await new Promise((r) => setTimeout(r, i % 3))
      const [del] = await Promise.all([
        fetch(`${base}/api/profiles/${id}`, { method: 'DELETE' }),
        patch,
      ])
      assert.equal(del.status, 200)
      assert.equal(fs.existsSync(path.join(PROFILES_DIR, id)), false, id)
    }
  })
  await clearProfiles()
})

test('参照音声を書けなければ、作りかけのプロファイルを残さない', async () => {
  const { DATA_ROOT, PROFILES_DIR } = await import('./server.ts')
  await clearProfiles()
  const gen = path.join(DATA_ROOT, 'generations')
  await mkdir(gen, { recursive: true })
  const take = path.join(gen, 'unreadable.wav')
  await writeFile(take, 'RIFF\0\0\0\0WAVE')
  await fs.promises.chmod(take, 0o000)
  try {
    await withServer(async (base) => {
      const res = await fetch(
        `${base}/api/profiles`,
        jsonInit('POST', { name: 'x', takes: ['generations/unreadable.wav'] }),
      )
      assert.equal(res.status, 500)
    })
    assert.deepEqual(await readdir(PROFILES_DIR).catch(() => []), [])
  } finally {
    await fs.promises.chmod(take, 0o644)
    await rm(take, { force: true })
    await clearProfiles()
  }
})

test('GET/PUT /api/styles は styles.json を読み書きし、重なる名前を断る', async () => {
  const { STYLES_FILE } = await import('./server.ts')
  const server = await startServer(0)
  const port = portOf(server)
  const put = (styles: unknown) =>
    fetch(`http://127.0.0.1:${port}/api/styles`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ styles }),
    })

  const empty = await readJson(await fetch(`http://127.0.0.1:${port}/api/styles`))
  assert.deepEqual(empty, { styles: [] })

  const dup = await put([
    { name: 'a', caption: null, sampler: {} },
    { name: 'a ', caption: null, sampler: {} },
  ])
  assert.equal(dup.status, 400)
  assert.equal((await put([{ name: 'a', caption: 1, sampler: {} }])).status, 400)
  assert.equal((await put([{ name: 'a', caption: null, sampler: { x: [1] } }])).status, 400)

  const styles = [
    { name: 'ささやき', caption: 'ささやく。', sampler: { duration_scale: 1.2 } },
    { name: 'ゆっくり', caption: null, sampler: {} },
  ]
  assert.equal((await put(styles)).status, 200)
  const got = await readJson(await fetch(`http://127.0.0.1:${port}/api/styles`))
  assert.deepEqual(got.styles, styles)
  // 常駐サーバーが読む形
  assert.deepEqual(JSON.parse(await readFile(STYLES_FILE, 'utf8')), { styles })
  await new Promise((resolve) => server.close(resolve))
})
