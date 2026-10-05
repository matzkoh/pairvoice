import crypto from 'node:crypto'
import fsp from 'node:fs/promises'
import path from 'node:path'

import { DATA_ROOT } from './paths.ts'

// 音声として配ってよいのは、データの置き場所の下の wav だけ。生成音声の置き場所
// （tts.output_dir）の名前は設定しだいなので場所では絞らず、拡張子で辞書・コーパス・
// プロファイルの JSON を外す。symlink は辿った先で判定する（リンクで外や wav でない
// ファイルを指させない）。当てはまらないパスは、存在しなかったのと区別せず null にして、
// 呼び出し側に 404 を返させる（脱出の可否を応答から読み取らせない）。
function isWavPath(p: string) {
  return path.extname(p).toLowerCase() === '.wav'
}

export function makeAudioPathResolver(root: string) {
  const resolvedRoot = path.resolve(root)
  // 置き場所自体の実体は変わらないので1回だけ引く。まだ無いうちの失敗は覚えない
  let realRoot: Promise<string> | null = null
  return async (relativePath: string) => {
    const candidate = path.resolve(resolvedRoot, relativePath)
    if (!isWavPath(candidate)) return null
    let real
    try {
      realRoot ??= fsp.realpath(resolvedRoot).catch((err: unknown) => {
        realRoot = null
        throw err
      })
      real = path.relative(await realRoot, await fsp.realpath(candidate))
    } catch {
      return null // 無い
    }
    // 外へ出たものは .. で始まる（別ボリュームなら絶対パスになる）
    if (path.isAbsolute(real) || real.split(path.sep)[0] === '..' || !isWavPath(real)) return null
    return candidate
  }
}

export const resolveAudioPath = makeAudioPathResolver(DATA_ROOT)

export async function atomicWrite(filePath: string, content: string) {
  await fsp.mkdir(path.dirname(filePath), { recursive: true })
  const tmp = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`,
  )
  await fsp.writeFile(tmp, content, 'utf8')
  await fsp.rename(tmp, filePath)
}

// JSONL は追記専用で、後の行が新しい。1回の呼び出しの行はまとめて1回で書く。
// 前の書き込みが途中で切れて末尾に改行が無ければ、改行を足してから書く（そのまま続けると
// 新しい行が壊れた行にくっつき、読む側がまとめて捨てる）
export async function appendJsonl(filePath: string, records: readonly unknown[]) {
  await fsp.mkdir(path.dirname(filePath), { recursive: true })
  const lines = records.map((record) => JSON.stringify(record) + '\n').join('')
  const handle = await fsp.open(filePath, 'a+')
  try {
    const { size } = await handle.stat()
    let separator = ''
    if (size > 0) {
      const last = Buffer.alloc(1)
      await handle.read(last, 0, 1, size - 1)
      if (last[0] !== 0x0a) separator = '\n'
    }
    await handle.appendFile(separator + lines, 'utf8')
  } finally {
    await handle.close()
  }
}

export async function readJsonlSafe<T>(filePath: string): Promise<T[]> {
  let raw
  try {
    raw = await fsp.readFile(filePath, 'utf8')
  } catch {
    return []
  }
  const out: T[] = []
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue
    try {
      out.push(JSON.parse(line))
    } catch {
      // 壊れた行はスキップする
    }
  }
  return out
}
