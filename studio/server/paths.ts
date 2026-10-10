import path from 'node:path'
import { fileURLToPath } from 'node:url'

// server.ts のあるディレクトリ（studio/）。server/ の1つ上
export const STUDIO_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
// 公開時はビルド済みの成果物だけを配信する。dist が無いのは
// 「開発中に :17494 を直接開いた」状態なので、503 で次の手を案内する。
export const DIST_DIR = path.join(STUDIO_DIR, 'web', 'dist')
// 定数ではなく毎回envを読む関数にしてある。テストが同一プロセス内で複数の
// pairvoice到達先（疎通不可なポート／スタブサーバー）を使い分けられるようにするため
// （constだとモジュール読み込み時の値に固定され、テストごとの差し替えができない）。
export function pairvoiceBase() {
  return process.env.PAIRVOICE_URL || 'http://127.0.0.1:17495'
}
export const DEFAULT_PORT = 17494
