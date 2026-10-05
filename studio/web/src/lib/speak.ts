// /speak を1回叩くところ。試聴と候補づくりのテイク生成はすべてここを通る。
//
// api.ts ではなく別ファイルに置くのは、テストが apiSend をモジュール境界で差し替える
// ため。api.ts の中から apiSend を呼ぶとその差し替えを素通りしてしまう。

import { apiSend, speakErrorMessage } from '@/lib/api'

import type { SamplerOverrides, SpeakResponse } from '../../../shared/api-types'

// design はプロファイル作成の候補づくり用。参照音声を使わず caption だけで声を作る。
// profile_id は試聴で鳴らすプロファイル。省くと使用中のプロファイルで鳴る
export type SpeakBody = {
  text: string
  caption?: string
  sampler?: SamplerOverrides
  design?: boolean
  profile_id?: string
}
// 合成した音声（データの置き場所からの相対パス）を鳴らす URL
export function audioFileUrl(relativePath: string) {
  return `/api/audio-file?path=${encodeURIComponent(relativePath)}`
}

export type SpeakSuccess = Extract<SpeakResponse, { relative_path: string }>

// 応答は「ミュートされた」「音が返らなかった」「鳴った」の3通りで、後ろ2つの見分け方を
// 呼び出し側に散らすと、応答の形が変わったときに片方だけ古い判定が残る。鳴らなかった
// 場合は必ず例外にして、成功の形だけを返す。
export async function speakOnce(body: SpeakBody): Promise<SpeakSuccess> {
  const result = await apiSend<SpeakResponse>('/api/speak', 'POST', body)
  if ('muted' in result && result.muted) throw new Error(`ミュート中（${result.reason}）`)
  if (!('relative_path' in result) || !result.relative_path) {
    throw new Error(speakErrorMessage(result))
  }
  return result
}
