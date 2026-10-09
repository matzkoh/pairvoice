import fsp from 'node:fs/promises'
import path from 'node:path'

import { HISTORY_DIR, PROMPT_FILE } from './paths.ts'

// フックは corpus.jsonl に「どの版のプロンプトで作った要約か」を書かない（フックは
// このリポジトリの外にあり、prompt.txt を読むだけ）。だが履歴の不変条件が
// 「かつて動いていた版はすべて履歴にある」なので、履歴の時刻から「その読み上げは
// いまの版で作られたか」を後付けで判定できる。過去の記録にも遡って効く。
export function parseHistoryTs(name: string) {
  // pairvoice が ISO の : と . を - に潰して付けた名前を、時刻に戻す
  const m = /^prompt-(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z\.txt$/.exec(name)
  if (!m) return null
  // マッチしていれば5つのキャプチャ群はすべて埋まっている（省略可能な群が無い）
  const ms = Date.parse(`${m[1]!}T${m[2]!}:${m[3]!}:${m[4]!}.${m[5]!}Z`)
  return Number.isNaN(ms) ? null : ms
}

// corpus.jsonl の ts はフックが date で書いたローカル時刻（"2026-07-28 14:01:59"）。
// オフセットの無い日時は ECMAScript ではローカル時刻として解釈されるので、
// 空白を T に替えるだけで履歴（UTC）と同じ数直線に乗る。
export function parseCorpusTs(ts: unknown) {
  if (typeof ts !== 'string') return null
  const ms = Date.parse(ts.replace(' ', 'T'))
  return Number.isNaN(ms) ? null : ms
}

// プロンプトの版は history/prompt-<ISO 時刻の : と . を - にしたもの>.txt。書くのは
// pairvoice（history.py）で、ここは読むだけ
async function listHistoryFiles() {
  let names: string[]
  try {
    names = await fsp.readdir(HISTORY_DIR)
  } catch {
    return [] // まだ1版も無い
  }
  return names
    .filter((f) => f.startsWith('prompt-') && f.endsWith('.txt'))
    .toSorted()
    .toReversed()
}

// いま動いているプロンプトが「その中身で動き出した」時刻（epoch ms）。
//
// 最新スナップショットの時刻を使ってはいけない。履歴は prompt.txt を書くたびに1件
// 増えるので、中身を変えずに保存し直しただけ・同じ内容の版に戻しただけでも時刻が進み、
// 直前まで現行だった読み上げが全部「旧プロンプト」に落ちてしまう（何も変えていないのに
// レビュー待ちが消える）。そこで新しい順にたどり、中身が現行と一致するスナップショットが
// 続く限り遡って、一致が途切れた次＝その中身が動き出した時刻を境界にする。
//
// 履歴が空のとき、prompt.txt が読めないとき、最新スナップショットとも中身が違うとき
// （studio を通さず手で書き換えた場合）は null を返す。いつから動いているか分からない
// のに境界を引くと、現行の出力を「旧」と誤って隠すことになる。
export async function currentPromptSince() {
  let current
  try {
    current = await fsp.readFile(PROMPT_FILE, 'utf8')
  } catch {
    return null
  }
  let since: number | null = null
  for (const name of await listHistoryFiles()) {
    const ts = parseHistoryTs(name)
    if (ts === null) continue
    let content
    try {
      content = await fsp.readFile(path.join(HISTORY_DIR, name), 'utf8')
    } catch {
      break
    }
    if (content !== current) break
    since = ts
  }
  return since
}
