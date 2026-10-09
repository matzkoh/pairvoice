import crypto from 'node:crypto'
import fsp from 'node:fs/promises'
import path from 'node:path'

import type {
  OkResponse,
  ProfileItem,
  ProfileSource,
  ProfilesResponse,
} from '../../shared/api-types.ts'
import { historyResponse, readHistoryVersion, snapshotTo } from '../history.ts'
import {
  badRequest,
  BodyTooLargeError,
  isRecord,
  notFound,
  readBodyBuffer,
  readJsonBody,
  sendJson,
  streamWav,
} from '../http.ts'
import { PROFILES_DIR } from '../paths.ts'
import type { AddRoute } from '../router.ts'
import { atomicWrite, resolveAudioPath } from '../storage.ts'
import { concatWavs, hasWavHeader, WavFormatError } from '../wav.ts'

// ---- 声のプロファイル ----
// profiles/<id>/{reference.wav, profile.json} と、使用中の ID を書いた profiles/active。
// pairvoice（profiles.py）も同じ形で読み書きする。pairvoice は合成のたびに読むので、
// ここで書き換えた内容は再起動なしに次の読み上げから効く。

const PROFILE_ACTIVE_FILE = path.join(PROFILES_DIR, 'active')
// profiles.py と同じ形。active や URL の :id でディレクトリの外へ出させない
export const PROFILE_ID_PATTERN = /^p-[0-9A-Za-z-]+$/
const PROFILE_SOURCES: readonly ProfileSource[] = ['design', 'upload', 'auto', 'import']
// 手持ちの wav を取り込むときの上限。数十秒の参照音声で足りる
const PROFILE_UPLOAD_MAX_BYTES = 50 * 1024 * 1024
// テイクをつなぐときの数の上限と、間に挟む無音
const PROFILE_MAX_TAKES = 8
const TAKE_GAP_SECONDS = 0.3

type ProfileMeta = Omit<ProfileItem, 'id'>

function profileReferencePath(id: string) {
  return path.join(PROFILES_DIR, id, 'reference.wav')
}

function profileMetaPath(id: string) {
  return path.join(PROFILES_DIR, id, 'profile.json')
}

// caption の版はプロファイルごとに持つ。プロファイルを消せば履歴も消える
function profileHistoryDir(id: string) {
  return path.join(PROFILES_DIR, id, 'history')
}

function newProfileId(now = new Date()) {
  // profiles.py と同じ p-<UTC 時刻 %Y%m%dT%H%M%SZ>-<乱数4桁>
  const ts = now
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\.\d+Z$/, 'Z')
  return `p-${ts}-${crypto.randomBytes(2).toString('hex')}`
}

// 参照音声が無いものはプロファイルとして扱わない（pairvoice も同じく読み飛ばす）
async function readProfile(id: string): Promise<ProfileItem | null> {
  if (!PROFILE_ID_PATTERN.test(id)) return null
  let meta: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(await fsp.readFile(profileMetaPath(id), 'utf8'))
    if (!isRecord(parsed)) return null
    meta = parsed
    if (!(await fsp.stat(profileReferencePath(id))).isFile()) return null
  } catch {
    return null
  }
  const source = PROFILE_SOURCES.find((s) => s === meta.source) ?? 'upload'
  return {
    id,
    name: typeof meta.name === 'string' ? meta.name : id,
    caption: typeof meta.caption === 'string' ? meta.caption : '',
    source,
    created_at: typeof meta.created_at === 'string' ? meta.created_at : '',
  }
}

async function readActiveProfileId() {
  try {
    const id = (await fsp.readFile(PROFILE_ACTIVE_FILE, 'utf8')).trim()
    return PROFILE_ID_PATTERN.test(id) ? id : null
  } catch {
    return null
  }
}

async function activeProfile() {
  const id = await readActiveProfileId()
  return id ? readProfile(id) : null
}

async function writeProfileMeta(id: string, meta: ProfileMeta) {
  await atomicWrite(profileMetaPath(id), JSON.stringify(meta, null, 2))
}

// 変えたい項目だけ差し替え、ほかは読んだまま書き戻す
async function updateProfileMeta({ id, ...meta }: ProfileItem, patch: Partial<ProfileMeta>) {
  await writeProfileMeta(id, { ...meta, ...patch })
}

// 読んで書き戻す処理を、プロファイルごとに1本の列で順に走らせる。重なると、後から
// 書いた側が先の変更を読む前の内容で上書きして消す（名前と caption を続けて変えたとき等）
const profileLocks = new Map<string, Promise<unknown>>()

function withProfileLock<T>(id: string, fn: () => Promise<T>): Promise<T> {
  const run = (profileLocks.get(id) ?? Promise.resolve()).then(fn)
  // 列の後続は前の失敗に巻き込まない。最後の1件が終わったら列ごと片付ける
  const tail: Promise<void> = run.then(
    () => undefined,
    () => undefined,
  )
  profileLocks.set(id, tail)
  void tail.finally(() => {
    if (profileLocks.get(id) === tail) profileLocks.delete(id)
  })
  return run
}

async function listProfiles(): Promise<ProfilesResponse> {
  let names: string[] = []
  try {
    names = await fsp.readdir(PROFILES_DIR)
  } catch {
    // まだ1つも無い
  }
  const items = (await Promise.all(names.map(readProfile))).filter(
    (item): item is ProfileItem => item !== null,
  )
  items.sort((a, b) => b.created_at.localeCompare(a.created_at))
  const active = await readActiveProfileId()
  return { items, active: items.some((item) => item.id === active) ? active : null }
}

// 使用中のプロファイルがまだ無ければ、作ったものを使用中にする。放っておくと次の
// 読み上げで pairvoice が既定の声を自動で作ってしまい、作った声が使われない
async function createProfile(
  meta: Omit<ProfileMeta, 'created_at'>,
  writeReference: (target: string) => Promise<void>,
) {
  const id = newProfileId()
  const dir = path.join(PROFILES_DIR, id)
  await fsp.mkdir(dir, { recursive: true })
  try {
    await writeReference(profileReferencePath(id))
    await writeProfileMeta(id, { ...meta, created_at: new Date().toISOString() })
  } catch (err) {
    // 作りかけを残すと、参照音声の無いディレクトリが溜まる（profiles.py と同じ扱い）
    await fsp.rm(dir, { recursive: true, force: true })
    throw err
  }
  if (!(await activeProfile())) await atomicWrite(PROFILE_ACTIVE_FILE, `${id}\n`)
  return readProfile(id)
}

export function registerProfileRoutes(addRoute: AddRoute) {
  addRoute('GET', '/api/profiles', async (req, res) => {
    sendJson(res, 200, await listProfiles())
  })

  // 2通りの作り方を1本で受ける。JSON なら試聴のテイク（pairvoice が合成した wav）を短い
  // 無音を挟んで1本の参照音声につなぎ（Irodori-TTS は同じ話者の短い発話を合わせて 30 秒ほどの
  // 参照音声を勧める）、audio/wav ならその本体を取り込む（名前と caption はクエリで渡す）
  addRoute('POST', '/api/profiles', async (req, res, ctx) => {
    const contentType = req.headers['content-type'] ?? ''
    if (contentType.startsWith('audio/')) {
      const name = ctx.query.get('name')?.trim() ?? ''
      const caption = ctx.query.get('caption')?.trim() ?? ''
      if (!name) return badRequest(res, 'name is required')
      // 申告で超えていれば読まずに断る。申告の無い chunked は読みながら数える
      const declared = Number(req.headers['content-length'])
      if (declared > PROFILE_UPLOAD_MAX_BYTES) throw new BodyTooLargeError('wav is too large')
      const audio = await readBodyBuffer(req, PROFILE_UPLOAD_MAX_BYTES)
      // 中身の妥当性は合成時に mlx-audio が判断する
      if (!hasWavHeader(audio)) return badRequest(res, 'body must be a wav file')
      const created = await createProfile({ name, caption, source: 'upload' }, (target) =>
        fsp.writeFile(target, audio),
      )
      return sendJson(res, 201, created)
    }

    const body = await readJsonBody(req)
    const name = typeof body.name === 'string' ? body.name.trim() : ''
    const caption = typeof body.caption === 'string' ? body.caption.trim() : ''
    if (!name) return badRequest(res, 'name is required')
    // 試聴のテイクは tts.output_dir（名前は設定しだい）に出る。他のプロファイルの参照音声を
    // 取り込み元にさせない
    const requested = Array.isArray(body.takes) ? body.takes : []
    if (requested.length === 0 || requested.length > PROFILE_MAX_TAKES) {
      return badRequest(res, 'takes (1 to 8 wav paths) is required')
    }
    const profilesDir = path.resolve(PROFILES_DIR) + path.sep
    const takes: string[] = []
    for (const relative of requested) {
      const take = typeof relative === 'string' ? await resolveAudioPath(relative) : null
      if (!take || take.startsWith(profilesDir)) return badRequest(res, 'valid take is required')
      try {
        if (!(await fsp.stat(take)).isFile()) return notFound(res)
      } catch {
        return notFound(res)
      }
      takes.push(take)
    }
    let joined: Buffer
    try {
      joined = concatWavs(await Promise.all(takes.map((t) => fsp.readFile(t))), TAKE_GAP_SECONDS)
    } catch (err) {
      if (err instanceof WavFormatError) return badRequest(res, `takes: ${err.message}`)
      throw err
    }
    const created = await createProfile({ name, caption, source: 'design' }, (target) =>
      fsp.writeFile(target, joined),
    )
    sendJson(res, 201, created)
  })

  addRoute('PUT', '/api/profiles/active', async (req, res) => {
    const body = await readJsonBody(req)
    const profile = typeof body.id === 'string' ? await readProfile(body.id) : null
    if (!profile) return notFound(res)
    await atomicWrite(PROFILE_ACTIVE_FILE, `${profile.id}\n`)
    sendJson(res, 200, { ok: true })
  })

  // 名前と caption を書き換える。caption は合成のたびに読まれるので、次の読み上げから効く
  addRoute('PATCH', '/api/profiles/:id', async (req, res, ctx) => {
    // 本文は列に並ぶ前に読む（遅い送り手に列を塞がせない）
    const body = await readJsonBody(req)
    const name = typeof body.name === 'string' ? body.name.trim() : undefined
    const caption = typeof body.caption === 'string' ? body.caption.trim() : undefined
    // caption は空でもよい（caption なしで読む）。名前は空にさせない
    if (name === '' || (name === undefined && caption === undefined)) {
      return badRequest(res, 'name (non-empty string) or caption (string) is required')
    }
    const updated = await withProfileLock(ctx.params.id!, async () => {
      const profile = await readProfile(ctx.params.id!)
      if (!profile) return null
      await updateProfileMeta(profile, {
        ...(name !== undefined && { name }),
        ...(caption !== undefined && { caption }),
      })
      if (caption !== undefined) {
        await snapshotTo('caption', profileHistoryDir(profile.id), caption)
      }
      return readProfile(profile.id)
    })
    if (!updated) return notFound(res)
    sendJson(res, 200, updated)
  })

  // PATCH と同じ列に並ぶ。消した後に PATCH が書き込むと、ディレクトリが作り直される
  addRoute('DELETE', '/api/profiles/:id', async (req, res, ctx) => {
    const result = await withProfileLock(ctx.params.id!, async () => {
      const profile = await readProfile(ctx.params.id!)
      if (!profile) return 'missing'
      // 使用中を消すと、次の読み上げで pairvoice が既定の声を黙って作り直す
      if (profile.id === (await readActiveProfileId())) return 'in_use'
      await fsp.rm(path.join(PROFILES_DIR, profile.id), { recursive: true, force: true })
      return 'ok'
    })
    if (result === 'missing') return notFound(res)
    if (result === 'in_use') {
      return sendJson(res, 409, {
        error: 'profile_in_use',
        message: '使用中のプロファイルは削除できません',
      })
    }
    sendJson(res, 200, { ok: true })
  })

  addRoute('GET', '/api/profiles/:id/audio', async (req, res, ctx) => {
    const profile = await readProfile(ctx.params.id!)
    if (!profile) return notFound(res)
    await streamWav(res, profileReferencePath(profile.id))
  })

  // caption の版は、書いた"後"に書いた内容そのもので撮る（プロンプトと同じ理由。書く前の
  // 版を残す作りだと、いま動いている版が常に履歴から漏れる）
  addRoute('GET', '/api/profiles/:id/caption/history', async (req, res, ctx) => {
    const profile = await readProfile(ctx.params.id!)
    if (!profile) return notFound(res)
    sendJson(res, 200, await historyResponse('caption', profileHistoryDir(profile.id)))
  })

  addRoute('POST', '/api/profiles/:id/caption/restore', async (req, res, ctx) => {
    const body = await readJsonBody(req)
    const result = await withProfileLock(ctx.params.id!, async () => {
      const profile = await readProfile(ctx.params.id!)
      if (!profile) return 'missing'
      const dir = profileHistoryDir(profile.id)
      const version = await readHistoryVersion('caption', dir, body.name)
      if (version === 'invalid' || version === 'missing') return version
      // 復元も履歴に1件増える。「その版が再び動き出した」記録として正しい
      await updateProfileMeta(profile, { caption: version.content })
      await snapshotTo('caption', dir, version.content)
      return 'ok'
    })
    if (result === 'invalid') return badRequest(res, 'valid history file name is required')
    if (result === 'missing') return notFound(res)
    const response: OkResponse = { ok: true }
    sendJson(res, 200, response)
  })
}
