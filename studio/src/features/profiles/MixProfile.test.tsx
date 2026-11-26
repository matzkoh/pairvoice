/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'

import { MixProfile } from './MixProfile'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const VOICE: ProfileItem = {
  id: 'p-a',
  name: '声A',
  caption: '',
  source: 'design',
  created_at: '1',
  tone: '',
  members: [],
  order: 'random',
  folded: false,
  speaker_model: '',
  mix: [],
}
const VOICES = [
  VOICE,
  { ...VOICE, id: 'p-b', name: '声B' },
  { ...VOICE, id: 'p-r', name: 'ランダム', source: 'random' as const },
]

function renderForm() {
  const onPlay = vi.fn()
  const onCreated = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MixProfile
        voices={VOICES}
        anchorText="試聴の文です。"
        pairvoiceDown={false}
        onPlay={onPlay}
        onStatus={vi.fn()}
        onCreated={onCreated}
      />
    </QueryClientProvider>,
  )
  fireEvent.change(screen.getByLabelText('声 A'), { target: { value: 'p-a' } })
  fireEvent.change(screen.getByLabelText('声 B'), { target: { value: 'p-b' } })
  return { onPlay, onCreated, slider: screen.getByLabelText('混ぜ具合') }
}

function synthesized() {
  return vi.mocked(apiSend).mock.calls.filter(([path]) => path === '/synthesize')
}

it('ランダムの声は混ぜる候補にしない', () => {
  renderForm()
  expect(screen.queryByRole('option', { name: 'ランダム' })).toBeNull()
})

it('スライダーを離すと、その位置の重みで声を指して試聴する', async () => {
  vi.mocked(apiSend).mockResolvedValue({ relative_path: 'generations/x.wav' })
  const { onPlay, slider } = renderForm()

  fireEvent.pointerDown(slider)
  // ドラッグ中は合成しない
  fireEvent.change(slider, { target: { value: '10' } })
  expect(synthesized()).toEqual([])
  await act(async () => {
    fireEvent.pointerUp(slider)
  })

  await waitFor(() => expect(onPlay).toHaveBeenCalledTimes(1))
  // 位置 0.1 は B の重み 0.25（目盛りを聞こえ方にそろえる）
  expect(synthesized()[0]![2]).toMatchObject({
    mix: [{ voice: 'p-a' }, { voice: 'p-b', weight: expect.closeTo(0.25, 2) }],
  })
})

it('合成の最中に動かした分は、終わってから最後の位置だけ頼む', async () => {
  const first = Promise.withResolvers<unknown>()
  vi.mocked(apiSend).mockImplementationOnce(() => first.promise)
  vi.mocked(apiSend).mockResolvedValue({ relative_path: 'generations/last.wav' })
  const { onPlay, slider } = renderForm()

  for (const value of ['20', '30', '40']) {
    fireEvent.pointerDown(slider)
    fireEvent.change(slider, { target: { value } })
    fireEvent.pointerUp(slider)
  }
  expect(synthesized()).toHaveLength(1)
  await act(async () => first.resolve({ relative_path: 'generations/first.wav' }))

  await waitFor(() => expect(synthesized()).toHaveLength(2))
  await waitFor(() => expect(onPlay).toHaveBeenCalledTimes(1))
  expect(onPlay.mock.calls[0]![0]).toContain('last.wav')
})

it('試聴と同じ caption・種・重みで保存する', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ...VOICE, id: 'p-m', name: '混ぜた声' })
  const { onCreated } = renderForm()

  fireEvent.change(screen.getByLabelText('caption'), { target: { value: ' 低め ' } })
  fireEvent.change(screen.getByLabelText('名前'), { target: { value: '混ぜた声' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'プロファイルを作る' }))
  })

  const saved = vi.mocked(apiSend).mock.calls.find(([path]) => path === '/profiles/mix')!
  expect(saved[2]).toMatchObject({
    name: '混ぜた声',
    caption: '低め',
    voices: [
      { voice: 'p-a', weight: 0.5 },
      { voice: 'p-b', weight: 0.5 },
    ],
    rng_seed: expect.any(Number),
  })
  expect(onCreated).toHaveBeenCalledWith('p-m')
})
