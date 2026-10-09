import { expect, it } from 'vitest'

import {
  answer,
  combine,
  dot,
  mapOf,
  mixOf,
  normalize,
  regionOf,
  roundOf,
  spreadOf,
  spreadsOf,
  type Step,
  type Voice,
  voiceOf,
  withVoices,
} from './voiceRegion'

// 決定的な乱数（線形合同法）
function seeded(seed = 1) {
  let s = seed
  return () => {
    s = (s * 1103515245 + 12345) % 2 ** 31
    return s / 2 ** 31
  }
}

function gaussian(random: () => number) {
  return Math.sqrt(-2 * Math.log(1 - random())) * Math.cos(2 * Math.PI * random())
}

// 本物の声の話者ベクトルは大きな共通成分を持つ（作った声どうしの cos は 0.81〜0.99、平均 0.92）。
// 第0軸を共通成分にし、残りの軸を声ごとの違いにする
const DIM = 16
const COMMON = 3.4
const randomVoice = (random: () => number, path: string) =>
  voiceOf(path, [
    COMMON,
    ...Array.from({ length: DIM - 1 }, () => gaussian(random) / Math.sqrt(DIM - 1)),
  ])
const mean = (xs: readonly number[]) => xs.reduce((s, v) => s + v, 0) / xs.length
// 声どうしの違いの部分（共通成分を除く）での近さ。人が聞き分けるのはこちら
const likeness = (x: readonly number[], y: readonly number[]) =>
  dot(normalize(x.slice(1)), normalize(y.slice(1)))
const unit = (k: number, dim = 4) => Array.from({ length: dim }, (_, i) => (i === k ? 1 : 0))
const sum = (w: readonly number[]) => w.reduce((s, v) => s + v, 0)
const sub = (x: readonly number[], y: readonly number[]) => x.map((v, i) => v - y[i]!)

function basisVoices(n: number) {
  return Array.from({ length: n }, (_, k) => voiceOf(`v${k}`, unit(k, n)))
}

// 望みの声 target に近い方を選ぶ利用者。人と同じく、2つの近さが似ているほど選び間違える。
// 聞いている間に、手持ちの声が1問に2本ずつ増える
function simulate(seed: number, rounds: number) {
  const random = seeded(seed)
  const target = randomVoice(random, 'target').vector
  const voices: Voice[] = [0, 1, 2].map((i) => randomVoice(random, `v${i}`))
  let region = regionOf([...voices], seed)
  const gaps: number[] = []
  const first = likeness(combine(region.voices, roundOf(region).center), target)
  for (let r = 1; r <= rounds; r++) {
    const round = roundOf(region)
    const [a, b] = [combine(region.voices, round.a), combine(region.voices, round.b)]
    gaps.push(1 - likeness(a, b))
    const lean = likeness(a, target) - likeness(b, target)
    region = answer(region, round, random() < 1 / (1 + Math.exp(-8 * lean)) ? 'a' : 'b')
    voices.push(randomVoice(random, `a${r}`), randomVoice(random, `b${r}`))
    region = withVoices(region, [...voices])
  }
  const last = likeness(combine(region.voices, roundOf(region).center), target)
  return { first, last, gaps }
}

const negative = (w: readonly number[]) => -w.reduce((acc, v) => acc + Math.min(0, v), 0)

it('A と B は、見立てを別々の手持ちの声へ大きく寄せた混ぜ合わせで、群れをほぼ半分に切る', () => {
  const region = regionOf(basisVoices(5), 1)
  const round = roundOf(region)
  for (const w of [round.center, round.a, round.b]) {
    expect(sum(w)).toBeCloseTo(1)
    // 外挿は全体の中に収める
    expect(negative(w)).toBeLessThanOrEqual(0.5 + 1e-9)
  }
  // A と B はそれぞれ別の1本の声に 7 割寄っている
  const [i, j] = round.pair
  expect(i).not.toBe(j)
  expect(round.a[i]! - 0.3 * round.center[i]!).toBeCloseTo(0.7)
  expect(round.b[j]! - 0.3 * round.center[j]!).toBeCloseTo(0.7)
  // 2つから等しく離れた面が、群れをほぼ半分に切る
  const [a, b] = [combine(region.voices, round.a), combine(region.voices, round.b)]
  const nearA = region.points.filter((p) => {
    const x = combine(region.voices, p)
    return dot(sub(x, b), sub(x, b)) > dot(sub(x, a), sub(x, a))
  }).length
  expect(Math.abs(nearA / region.points.length - 0.5)).toBeLessThan(0.25)
  // 合成に渡す混ぜ具合は、小さすぎる重みを落として和を1にそろえる
  const mix = mixOf(region.voices, round.a)
  expect(mix.every((part) => Math.abs(part.weight) >= 0.02)).toBe(true)
  expect(sum(mix.map((part) => part.weight))).toBeCloseTo(1, 2)
})

it('前の問いと同じ組は続けて出さない', () => {
  let region = regionOf(basisVoices(5), 2)
  let prev = roundOf(region)
  for (let k = 0; k < 6; k++) {
    region = answer(region, prev, 'a')
    const round = roundOf(region)
    expect(round.pair).not.toEqual(prev.pair)
    prev = round
  }
})

it('答えると、群れは選んだ側へ絞られて狭まる', () => {
  const region = regionOf(basisVoices(4), 1)
  const round = roundOf(region)
  const next = answer(region, round, 'a')
  const center = (r: typeof region) => combine(r.voices, roundOf(r).center)
  const [a, b] = [combine(region.voices, round.a), combine(region.voices, round.b)]
  const toward = (x: readonly number[]) => dot(x, a) - dot(x, b)
  expect(toward(center(next))).toBeGreaterThan(toward(center(region)))
  expect(spreadOf(next)).toBeLessThan(spreadOf(region))
  // 同じ答えなら同じ群れになる（戻ってやり直しても結果が変わらない）
  expect(answer(region, round, 'a').points).toEqual(next.points)
})

it('後から加わった声も、答えに合えば群れに入る', () => {
  const voices = basisVoices(5)
  const region = withVoices(regionOf(voices.slice(0, 3), 1), voices)
  expect(region.points.every((p) => p.length === 5 && p[3] === 0)).toBe(true)
  const next = answer(region, roundOf(region), 'skip')
  expect(next.points.some((p) => p[3]! > 0.1 || p[4]! > 0.1)).toBe(true)
  // 点はどれも全体の中（負の重みの合計が上限以下）
  expect(next.points.every((p) => negative(p) <= 0.5 + 1e-9)).toBe(true)
})

it('揺らぐ答えのまま望みの声に近づき、A と B は最後まではっきり違う', () => {
  const runs = [1, 2, 3, 4, 5, 6, 7, 8].map((seed) => simulate(seed, 15))
  // 同じ揺らぎの利用者で、範囲の端の2本を A と B にした作りは 20 問で 0.34、見立てを挟んで
  // 対称に離した作りは 0.38 だった。この作りは 0.47
  expect(mean(runs.map((r) => r.last))).toBeGreaterThan(mean(runs.map((r) => r.first)) + 0.2)
  // 声どうしの違いの部分で cos が 0 を下回る（まるで違う2人の声）ほど離れたまま
  expect(mean(runs.map((r) => r.gaps.at(-1)!))).toBeGreaterThan(0.8)
})

it('地図は、B から A への向きを横軸にして、手持ちの声と群れと見立ての道のりを描く', () => {
  const voices = basisVoices(4)
  const region = regionOf(voices, 1)
  const steps: Step[] = [{ region, round: roundOf(region) }]
  const next = answer(region, steps[0]!.round, 'b')
  steps.push({ region: next, round: roundOf(next) })
  const map = mapOf(steps)
  expect(map.voices).toHaveLength(4)
  expect(map.trail).toHaveLength(2)
  expect(map.region.length).toBeGreaterThan(100)
  // A は右、B は左で、同じ高さ。見立ては原点
  expect(map.a[0]).toBeGreaterThan(0)
  expect(map.b[0]).toBeLessThan(0)
  expect(map.a[1]).toBeCloseTo(map.b[1])
  expect(map.trail.at(-1)![0]).toBeCloseTo(0)
  const spreads = spreadsOf(steps)
  expect(spreads[0]).toBe(1)
  expect(spreads[1]).toBeLessThan(1)
})
