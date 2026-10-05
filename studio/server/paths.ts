import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// server.ts のあるディレクトリ（studio/）。server/ の1つ上
export const STUDIO_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
// データの置き場所。実行時に読み書きされるもの（プロンプト・辞書・コーパス・音声）を置く。
// フックも同じディレクトリを見るため、git 作業ツリーとは独立している必要がある。
export const DATA_ROOT =
  process.env.PAIRVOICE_DATA_ROOT ||
  path.join(os.homedir(), 'Library', 'Application Support', 'pairvoice')
// 公開時はビルド済みの成果物だけを配信する。dist が無いのは
// 「開発中に :17494 を直接開いた」状態なので、503 で次の手を案内する。
export const DIST_DIR = path.join(STUDIO_DIR, 'web', 'dist')
// studio 自身の成果物もデータの置き場所に置く。リポジトリが追跡するのはコードと
// ドキュメントだけにするため。
export const HISTORY_DIR = path.join(DATA_ROOT, 'history')
export const CORPUS_FILE = path.join(DATA_ROOT, 'corpus.jsonl')
export const REVIEWS_FILE = path.join(DATA_ROOT, 'reviews.jsonl')
export const ARCHIVES_FILE = path.join(DATA_ROOT, 'archives.jsonl')
export const DICT_FILE = path.join(DATA_ROOT, 'dict.tsv')
export const PROMPT_FILE = path.join(DATA_ROOT, 'prompt.txt')
export const PROFILES_DIR = path.join(DATA_ROOT, 'profiles')
// 定数ではなく毎回envを読む関数にしてある。テストが同一プロセス内で複数の
// pairvoice到達先（疎通不可なポート／スタブサーバー）を使い分けられるようにするため
// （constだとモジュール読み込み時の値に固定され、テストごとの差し替えができない）。
export function pairvoiceBase() {
  return process.env.PAIRVOICE_URL || 'http://127.0.0.1:17495'
}
export const DEFAULT_PORT = 17494
