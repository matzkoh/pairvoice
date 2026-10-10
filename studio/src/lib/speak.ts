// /synthesize を1回叩くところ。試聴と候補づくりのテイク生成はすべてここを通る。
//
// api.ts ではなく別ファイルに置くのは、テストが apiSend をモジュール境界で差し替える
// ため。api.ts の中から apiSend を呼ぶとその差し替えを素通りしてしまう。

import { apiSend, apiUrl, speakErrorMessage } from '@/lib/api'
import type { SamplerOverrides, SpeakResponse } from '@/lib/api-types'

// design はプロファイル作成の候補づくり用。参照音声を使わず caption だけで声を作る。
// voice は試聴で鳴らすプロファイル。省くと使用中のプロファイルで鳴る
export type SpeakBody = {
  text: string
  caption?: string
  sampler?: SamplerOverrides
  design?: boolean
  voice?: string
  // 2択で絞り込むときの、もとの声（試聴で作った wav）を重みで混ぜた声
  mix?: { audio: string; weight: number }[]
}
// 合成した音声（データの置き場所からの相対パス）を鳴らす URL
export function audioFileUrl(relativePath: string) {
  return apiUrl(`/audio?path=${encodeURIComponent(relativePath)}`)
}

// 音が返らなかった場合は必ず例外にして、成功の形だけを返す。見分け方を呼び出し側に
// 散らすと、応答の形が変わったときに一部だけ古い判定が残る
export async function speakOnce(body: SpeakBody, signal?: AbortSignal): Promise<SpeakResponse> {
  // 打ち切らない呼び出しは、これまでどおり3引数で送る
  const result = signal
    ? await apiSend<SpeakResponse>('/synthesize', 'POST', body, { signal })
    : await apiSend<SpeakResponse>('/synthesize', 'POST', body)
  if (!('relative_path' in result) || !result.relative_path) {
    throw new Error(speakErrorMessage(result))
  }
  return result
}
