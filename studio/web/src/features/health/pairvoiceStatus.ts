import type { PairvoiceHealth } from '@/lib/api-types'

// 赤緑2値にせず理由を文字で出すのが運用の核心要件で、「読み上げが来ない理由の
// 切り分け」がこの1行に集約されている。
//
// 早いものが勝つ if-else chain で、順序そのものが仕様。ミュートはモデルの状態より
// 先に出す（黙っている理由としてはミュートのほうが上位）。表示から切り離した純関数に
// してあるのは、この順序をテストで固定するため。
export type StatusTone = 'ok' | 'warn' | 'bad'
export type PairvoiceStatus = { tone: StatusTone; label: string }

// 破棄が続いているときだけ詰まりとみなす閾値。
const DROPPED_RECENT_THRESHOLD = 3

export function pairvoiceStatus(health: PairvoiceHealth | null): PairvoiceStatus {
  if (!health) return { tone: 'bad', label: 'pairvoice 停止' }

  const states = [health.llm.state, health.tts.state]

  if (health.mute.active) return { tone: 'warn', label: `ミュート中（${health.mute.reason}）` }
  if (states.includes('downloading')) return { tone: 'warn', label: 'モデル取得中' }
  if (states.includes('loading')) return { tone: 'warn', label: 'ロード中' }
  if (states.includes('failed') || states.includes('misconfigured')) {
    // 両方揃っているときは misconfigured を優先する。設定の不備は人が直せるもので、
    // ロード失敗より先に知らせる価値があるため。
    return { tone: 'bad', label: states.includes('misconfigured') ? '設定不備' : 'ロード失敗' }
  }
  // 読み込み済みの detail は、重みは載ったまま使えない理由（参照音声が消えた等）。読み上げは
  // 503 になっているので、loaded の見た目のまま健全に見せない
  const broken = [health.llm, health.tts].find((m) => m.state === 'loaded' && m.detail)
  if (broken) return { tone: 'bad', label: `設定不備（${broken.detail}）` }
  if (health.dropped_recent >= DROPPED_RECENT_THRESHOLD) {
    return { tone: 'warn', label: `キュー詰まり（${health.dropped_recent}件破棄）` }
  }
  if (health.config_stale) return { tone: 'warn', label: '設定が未反映（再起動が必要）' }

  return { tone: 'ok', label: 'pairvoice' }
}
