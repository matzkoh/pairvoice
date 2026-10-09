import type { PairvoiceHealth } from '../../../../shared/api-types'

// pairvoice が古いと `/health` の tts に sampler キー自体が無い（新設フィールドなので、
// 旧サーバーの応答には存在しない）。この状態で sampler を送っても、
// 旧 SpeakRequest（Pydantic の既定は extra="ignore"）に黙って捨てられ、ノブを
// 何度動かしても同じ音が返る。config_stale は設定ファイルの古さしか申告しないので
// これは捕まえられない。
//
// pairvoice 自体が停止しているとき（pairvoice が null）は別の扱い（既存の「停止」表示）
// に任せるので、ここでは「動いてはいるが古い」場合だけ true を返す。
export function pairvoiceOutdated(pairvoice: PairvoiceHealth | null): boolean {
  return pairvoice !== null && pairvoice.tts.sampler === undefined
}
