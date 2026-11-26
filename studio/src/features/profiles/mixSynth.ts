import { queryOptions } from '@tanstack/react-query'

import { apiSend } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'
import { type MixSource, speakOnce } from '@/lib/speak'

// 混ぜた声を試聴用に合成する（読み上げと同じ段数）
export async function synth(
  text: string,
  caption: string,
  seed: number,
  mix: readonly MixSource[],
) {
  return (await speakOnce({ text, caption, sampler: { rng_seed: seed }, mix: [...mix] }))
    .relative_path
}

// 合成した音声は (文, caption, seed, 混ぜ具合) で決まる。戻ったときに取り直さない
export function mixQueryOptions(
  text: string,
  caption: string,
  seed: number,
  mix: readonly MixSource[],
) {
  return queryOptions({
    queryKey: ['pick-mix', text, caption, seed, mix] as const,
    queryFn: async () => ({ relative_path: await synth(text, caption, seed, mix) }),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })
}

// 選んだテイクの声を残す。サーバーがその声で決まった文も読ませて参照音声にし、声の表現も保存する。
// 作った声の caption は空にする（声を作るための描写は、読み上げでは声を言い直すだけ。話し方を
// 足したければ後で書ける。createFromMix も同じ）
export function createFromTake(name: string, take: string, seed: number) {
  return apiSend<ProfileItem>('/profiles', 'POST', {
    name,
    caption: '',
    takes: [take],
    extend: true,
    rng_seed: seed,
  })
}

// 混ぜた話者の表現をそのまま声として残す（合成した音声から作り直すと、聞いた声からずれる）。
// 参照音声はサーバーが同じ声で決まった文を読ませて作る
export function createFromMix(name: string, mix: readonly MixSource[], seed: number) {
  return apiSend<ProfileItem>('/profiles/mix', 'POST', {
    name,
    caption: '',
    voices: mix,
    rng_seed: seed,
  })
}
