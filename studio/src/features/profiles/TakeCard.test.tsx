/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import { initialValues } from './samplerKnobs'
import { TakeCard } from './TakeCard'
import type { Take } from './useTakes'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function take(overrides: Partial<Take>): Take {
  return {
    id: 3,
    seed: 42,
    status: 'done',
    relativePath: 'generations/x.wav',
    duration: 2.4,
    input: { text: 'テスト', caption: '明るく', values: initialValues(), sampler: {} },
    ...overrides,
  }
}

function renderCard(t: Take, adoptedCaption: string) {
  const onAdopted = vi.fn()
  const onPlay = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ul>
        <TakeCard
          take={t}
          profileId="p-a"
          adoptedCaption={adoptedCaption}
          canRetry
          onPlay={onPlay}
          onRetry={vi.fn()}
          onAdopted={onAdopted}
        />
      </ul>
    </QueryClientProvider>,
  )
  return { onAdopted, onPlay }
}

it('seed と長さを出し、再生できる', () => {
  const { onPlay } = renderCard(take({}), '明るく')
  expect(screen.getByText('seed 42')).toBeTruthy()
  expect(screen.getByText('2.4 秒')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'テイク 3 を再生' }))
  expect(onPlay).toHaveBeenCalled()
})

it('採用中と同じ caption のテイクは採用ボタンを押せない', () => {
  renderCard(take({}), '明るく')
  expect(screen.getByRole('button', { name: 'この caption を採用' })).toHaveProperty(
    'disabled',
    true,
  )
})

it('caption を変えたテイクは「変更あり」で、2回押すとそのテイクの caption を書く', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const { onAdopted } = renderCard(take({}), '落ち着いて')
  expect(screen.getByText(/caption 変更あり/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'この caption を採用' }))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '採用してよいですか' }))
  })
  expect(apiSend).toHaveBeenCalledWith('/profiles/p-a', 'PATCH', { caption: '明るく' })
  expect(onAdopted).toHaveBeenCalledWith({
    message: '採用しました（次の読み上げから効きます）',
    isError: false,
  })
})

it('失敗したテイクは理由を出す', () => {
  renderCard(
    take({ status: 'error', relativePath: undefined, message: 'model_load_failed' }),
    '明るく',
  )
  expect(screen.getByText('model_load_failed')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'テイク 3 を再生' })).toHaveProperty('disabled', true)
})

it('「コピーしました」は少しして元の文言に戻る', async () => {
  vi.useFakeTimers()
  try {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    })
    const values = initialValues({ num_steps: 12 })
    renderCard(take({ input: { ...take({}).input, values } }), '明るく')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'サンプラーを TOML でコピー' }))
    })
    expect(screen.getByRole('button', { name: 'コピーしました' })).toBeTruthy()
    await act(async () => {
      vi.advanceTimersByTime(2000)
    })
    expect(screen.getByRole('button', { name: 'サンプラーを TOML でコピー' })).toBeTruthy()
  } finally {
    vi.useRealTimers()
  }
})
