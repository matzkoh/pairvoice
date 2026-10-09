/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { apiSend, isRecord } from '@/lib/api'

import { REFERENCE_TEXTS } from './mixSynth'
import { FIRST_VOICES, PickProfile } from './PickProfile'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

// jsdom の <audio> は鳴らない。play / pause が paused と再生のイベントを動かすところだけ真似る
// （play はすぐ鳴り始めたものとして playing を送る）
function setPaused(el: HTMLMediaElement, paused: boolean) {
  Object.defineProperty(el, 'paused', { value: paused, configurable: true })
}

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    setPaused(this, false)
    this.dispatchEvent(new Event('playing'))
    return Promise.resolve()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    setPaused(this, true)
    this.dispatchEvent(new Event('pause'))
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

function player(): HTMLAudioElement {
  const el = document.querySelector('audio')
  if (!el) throw new Error('no audio')
  return el
}

// 鳴っている音が終わったときのブラウザの振る舞い
function finishPlaying() {
  const el = player()
  setPaused(el, true)
  el.dispatchEvent(new Event('pause'))
  el.dispatchEvent(new Event('ended'))
}

function playingCard() {
  return screen.queryByText('再生中')?.closest('[aria-current="true"]') ?? null
}

type SpeakCall = {
  text: string
  caption: string
  seed: number
  steps: unknown
  design: boolean
  mix?: { audio: string; weight: number }[]
}

function speakCalls(): SpeakCall[] {
  return vi
    .mocked(apiSend)
    .mock.calls.filter(([path]) => path === '/api/speak')
    .map(([, , body]) => {
      if (!isRecord(body) || !isRecord(body.sampler)) throw new Error('unexpected body')
      return {
        text: String(body.text),
        caption: String(body.caption),
        seed: Number(body.sampler.rng_seed),
        steps: body.sampler.num_steps,
        design: body.design === true,
        ...(Array.isArray(body.mix) && {
          mix: body.mix.map((part: unknown) => {
            if (!isRecord(part)) throw new Error('unexpected mix')
            return { audio: String(part.audio), weight: Number(part.weight) }
          }),
        }),
      }
    })
}

// A と B の合成（混ぜた声）
const mixCalls = () => speakCalls().filter((call) => call.mix)
const designCalls = () => speakCalls().filter((call) => call.design)
const wav = (n: number) => `generations/${n}.wav`
// いちばん重く混ぜている声
const main = (mix: readonly { audio: string; weight: number }[]) =>
  mix.reduce((top, part) => (part.weight > top.weight ? part : top))

// プロファイルの画面の再生（いまの見立てを鳴らすのに使う）
const onPlay = vi.fn()

async function startPicking(known: Record<string, string> = {}) {
  let n = 0
  let anchor = 0
  vi.mocked(apiSend).mockImplementation(async (path: string) => {
    if (path === '/api/speaker-vector') {
      // 声ごとに向きの違う話者ベクトル
      const i = anchor++
      return { vector: Array.from({ length: 6 }, (_, j) => (i === j ? 1 : 0.2)) }
    }
    return { relative_path: `generations/${++n}.wav`, duration: 3 }
  })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PickProfile
        anchorText="読ませる文です。"
        pairvoiceDown={false}
        onPlay={onPlay}
        onStatus={vi.fn()}
        onCreated={vi.fn()}
      />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: '始める' }))
  // 事前の質問は1軸ずつ。答えたい軸だけ選び、残りは聞き比べで決める
  for (const [axis, level] of Object.entries(known)) {
    expect(screen.getByRole('heading', { name: `${axis}は決まっていますか？` })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: level }))
  }
  fireEvent.click(screen.getByRole('button', { name: '残りは聞き比べで決める' }))
  await waitFor(() => expect(mixCalls()).toHaveLength(2))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'A が近い' })).toHaveProperty('disabled', false),
  )
}

it('答えた軸の声と、残りをでたらめにした声を作り、A と B は見立てを別々の声へ大きく寄せて混ぜる', async () => {
  await startPicking({ 性別: '女性' })

  // 始める前に3人。1人目は答えた軸だけの caption
  const first = designCalls().slice(0, FIRST_VOICES)
  expect(first).toHaveLength(FIRST_VOICES)
  expect(first[0]!.caption).toBe('女性の声。')
  for (const call of first) expect(call.caption).toMatch(/女性の声。/)

  const [a, b] = mixCalls()
  // 混ぜた声には答えた軸だけを caption で伝え、声の個体は混ぜ具合が決める
  expect(a!.caption).toBe('女性の声。')
  expect(b!.seed).toBe(a!.seed)
  // A と B は見立て（はじめは3人の混ぜ合わせ）を、それぞれ別の1人へ大きく寄せる
  const firstThree = [wav(1), wav(2), wav(3)]
  for (const call of [a!, b!]) {
    expect(call.mix!.every((part) => firstThree.includes(part.audio))).toBe(true)
    expect(call.mix!.reduce((s, part) => s + part.weight, 0)).toBeCloseTo(1, 2)
  }
  expect(main(a!.mix!).weight).toBeGreaterThan(0.7)
  expect(main(b!.mix!).weight).toBeGreaterThan(0.7)
  expect(main(a!.mix!).audio).not.toBe(main(b!.mix!).audio)
})

it('A に続けて B を鳴らし、鳴っている側のカードを示す', async () => {
  await startPicking()

  // B ができた時点では A を鳴らしている最中なので、A が終わってから B を鳴らす
  const [first, second] = [FIRST_VOICES + 1, FIRST_VOICES + 2]
  expect(player().src).toContain(`generations%2F${first}.wav`)
  expect(playingCard()?.textContent).toContain('A が近い')
  act(() => finishPlaying())
  expect(player().src).toContain(`generations%2F${second}.wav`)
  expect(playingCard()?.textContent).toContain('B が近い')
  act(() => finishPlaying())
  expect(playingCard()).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: /AB を続けて再生/ }))
  expect(player().src).toContain(`generations%2F${first}.wav`)
})

it('答えたら候補を絞って次の2択を作り（合成した音声は混ぜない）、戻ったら前の2択をそのまま出す', async () => {
  await startPicking()

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'B が近い' }))
  })
  await waitFor(() => expect(screen.getByText('第2問')).toBeTruthy())
  await waitFor(() => expect(mixCalls().length).toBeGreaterThanOrEqual(3))
  const next = mixCalls()[2]!
  // 混ぜるのは caption から作ったきれいな声だけ。聞かせた A と B の音声は混ぜない
  const heard = [wav(FIRST_VOICES + 1), wav(FIRST_VOICES + 2)]
  expect(next.mix!.some((part) => heard.includes(part.audio))).toBe(false)
  expect(next.mix!.every((part) => part.weight > 0)).toBe(true)
  expect(next.mix).not.toEqual(mixCalls()[0]!.mix)

  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'ひとつ戻る' })).toHaveProperty('disabled', false),
  )
  const before = mixCalls().length
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'ひとつ戻る' }))
  })
  expect(screen.getByText('第1問')).toBeTruthy()
  // 戻った問いの A と B は取り直さない
  expect(mixCalls()).toHaveLength(before)
})

it('どちらとも言えないときも、次の問いへ進む', async () => {
  await startPicking()

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'どちらとも言えない' }))
  })
  await waitFor(() => expect(mixCalls().length).toBeGreaterThanOrEqual(3))
  expect(screen.getByText('第2問')).toBeTruthy()
})

it('A と B を聞いている間に、手持ちの声を増やしておく', async () => {
  await startPicking()

  await waitFor(() => expect(designCalls().length).toBeGreaterThan(FIRST_VOICES))
})

it('見つけた声に決めると、同じ混ぜ具合で合成の段数を増やし、別の文も読ませて参照音声にする', async () => {
  await startPicking()
  // A と B はわざと理想から外した声なので、保存できるのは見つけた声だけ
  expect(screen.queryByRole('button', { name: 'A を保存' })).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: '見つけた声 を保存' }))
  fireEvent.change(screen.getByRole('textbox', { name: '見つけた声 の名前' }), {
    target: { value: '見つけた声' },
  })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'この声で作る' }))
  })

  await waitFor(() =>
    expect(vi.mocked(apiSend).mock.calls.some(([p]) => p === '/api/profiles')).toBe(true),
  )
  const remastered = speakCalls().filter((call) => call.steps === 80)
  expect(remastered.map((call) => call.text)).toEqual(['読ませる文です。', ...REFERENCE_TEXTS])
  // 見つけた声（はじめは3人の混ぜ合わせ）のまま読ませる
  for (const call of remastered) {
    expect(call.mix!.map((part) => part.audio)).toEqual([wav(1), wav(2), wav(3)])
    expect(call.mix).toEqual(remastered[0]!.mix)
  }
  const saved = vi.mocked(apiSend).mock.calls.find(([p]) => p === '/api/profiles')![2]
  // 作り直した音声（試聴の A の wav ではない）をつないで参照音声にする
  // 声は参照音声が決めるので、caption は空
  expect(saved).toMatchObject({ name: '見つけた声', caption: '' })
  const takes = isRecord(saved) && Array.isArray(saved.takes) ? saved.takes : []
  expect(takes).toHaveLength(REFERENCE_TEXTS.length + 1)
  expect(takes).not.toContain(wav(FIRST_VOICES + 1))
})

it('見つけた声は、聴くと決めたときだけ合成して鳴らす', async () => {
  await startPicking()
  const before = mixCalls().length

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '聴く' }))
  })
  await waitFor(() => expect(mixCalls()).toHaveLength(before + 1))
  // 最初に見つけた声は、始める前に作った3人の混ぜ合わせ
  expect(
    mixCalls()
      .at(-1)!
      .mix!.map((part) => part.audio),
  ).toEqual([wav(1), wav(2), wav(3)])
  // 2択とは別の、プロファイルの画面の再生で鳴らす
  await waitFor(() =>
    expect(onPlay).toHaveBeenCalledWith(expect.stringContaining('generations%2F')),
  )
  expect(screen.getByRole('button', { name: '見つけた声 を保存' })).toBeTruthy()
})

it('答えたら、鳴っている音と続けて鳴らす予定を止める', async () => {
  await startPicking()
  // A を鳴らしている最中（B は A の後に続ける予定）
  expect(playingCard()?.textContent).toContain('A が近い')
  const before = player()
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'B が近い' }))
  })
  expect(before.paused).toBe(true)
})
