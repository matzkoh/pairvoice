import type { CSSProperties } from 'react'

// 声ごとの色と形を、声の ID から決める。保存はしない（同じ ID なら、どの画面でも同じ色と形になる）。
// 声が増えれば色は近いものが出るので、色と形は当たりを付けるためのもので、確かめるのは名前

// FNV-1a。短い ID でもばらけ、実行ごとに変わらない
function hash(text: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

// 蛍光ペン（--mark）と紛れる黄の帯。声の色はここを飛ばして振る
const SKIP_FROM = 75
const SKIP_TO = 125

// oklch の色相（度）
export function voiceHue(id: string): number {
  const span = 360 - (SKIP_TO - SKIP_FROM)
  const h = hash(id) % span
  return h < SKIP_FROM ? h : h + (SKIP_TO - SKIP_FROM)
}

// 濃淡の段（0 は明るい、1 は濃い）。色相とは別のビットから取る
export function voiceDeep(id: string): 0 | 1 {
  return hash(`${id}:deep`) % 2 === 0 ? 0 : 1
}

// 声の色を、その要素と子孫の text-voice / bg-voice に効かせる
export function voiceStyle(id: string | null | undefined): CSSProperties | undefined {
  if (!id) return undefined
  const style: CSSProperties & { '--voice-h': number; '--voice-deep': number } = {
    '--voice-h': voiceHue(id),
    '--voice-deep': voiceDeep(id),
  }
  return style
}

export const VOICE_BARS = 5

// 声の印の棒の高さ（0.3〜1）。両端は低め、中ほどは高めに寄せて、どの声も波の形に見せる
export function voiceBars(id: string): number[] {
  let seed = hash(`${id}:bars`)
  return Array.from({ length: VOICE_BARS }, (_, i) => {
    seed = Math.imul(seed ^ (seed >>> 15), 0x2c1b3c6d) >>> 0
    const random = (seed % 1000) / 1000
    const edge = i === 0 || i === VOICE_BARS - 1
    const [low, high] = edge ? [0.3, 0.65] : [0.5, 1]
    return low + random * (high - low)
  })
}
