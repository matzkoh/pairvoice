import { queryOptions } from '@tanstack/react-query'

import { speakOnce } from '@/lib/speak'

import type { MixPart } from './voiceRegion'

// 保存する声の合成の段数（モデル既定は 40）。増やすほど丁寧で遅い
export const MASTER_STEPS = 80

// 混ぜた声を合成する。試聴は速さ優先でモデル既定の段数、保存する声は numSteps を増やす
export async function synth(
  text: string,
  caption: string,
  seed: number,
  mix: readonly MixPart[],
  numSteps?: number,
) {
  const sampler =
    numSteps === undefined ? { rng_seed: seed } : { rng_seed: seed, num_steps: numSteps }
  return (await speakOnce({ text, caption, sampler, mix: [...mix] })).relative_path
}

// 合成した音声は (文, caption, seed, 混ぜ具合) で決まる。戻ったときに取り直さない
export function mixQueryOptions(
  text: string,
  caption: string,
  seed: number,
  mix: readonly MixPart[],
) {
  return queryOptions({
    queryKey: ['pick-mix', text, caption, seed, mix] as const,
    queryFn: async () => ({ relative_path: await synth(text, caption, seed, mix) }),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })
}

// 参照音声に足して読ませる文。Irodori-TTS は同じ話者の短い発話を合わせて 30 秒ほどの参照
// 音声を勧めるので、試聴した文（7〜8 秒）にこの3つ（各 6〜7 秒）を足してつなぐ。
// 常駐サーバーの POST /profiles の extend（src/pairvoice/data_api.py）と同じ文にしておく
// - 読み上げるのはエージェントの作業の要約なので、落ち着いた説明調を軸にし、問いかけと
//   軽い相づちで抑揚に幅を持たせる。参照音声の話し方は複製した声に移るので、強い感情は入れない
// - 拗音（しゅ・ちょ・じゅ）、促音、撥音、長音、濁音・半濁音、カタカナ語と数を一通り含める
export const REFERENCE_TEXTS = [
  'ビルドとテストはすべて通りました。変更は三つのファイルにまとまっていて、再起動も済んでいます。',
  'ひとつ確認させてください。この設定は、来週のアップデートまでに切り替えておけば間に合いますか？',
  'ちょっと待ってくださいね。原因はわかったので、じゅうぶん直せそうです。順番に片づけましょう。',
] as const

// 参照音声のテイクを1文ずつ作る（まとめて頼むと Runner を塞ぎ、フックの読み上げが待たされる）。
// made はすでにあるテイクで、作ったテイクをその後ろに足して返す。progress は作り終えた数と
// 全体の数
export async function recordTakes(
  texts: readonly string[],
  speak: (text: string) => Promise<string>,
  progress: (done: number, total: number) => void,
  made: readonly string[] = [],
) {
  const takes = [...made]
  const total = made.length + texts.length
  for (const text of texts) {
    progress(takes.length, total)
    takes.push(await speak(text))
  }
  return takes
}
