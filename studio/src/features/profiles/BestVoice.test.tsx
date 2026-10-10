/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import { BestVoice } from './BestVoice'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderAt(narrowed: number) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BestVoice
        mix={[{ audio: 'generations/1.wav', weight: 1 }]}
        text="読ませる文です。"
        caption=""
        seed={1}
        narrowed={narrowed}
        onPlay={vi.fn()}
        onStatus={vi.fn()}
        onCreated={vi.fn()}
      />
    </QueryClientProvider>,
  )
  return screen.getByRole('button', { name: '見つけた声 を保存' })
}

it('候補の範囲が狭まるまでは控えめで、狭まったら「この声に決める」を主ボタンにする', () => {
  expect(renderAt(0.1).className).not.toMatch(/\bbg-primary\b/)
  cleanup()
  const ready = renderAt(0.6)
  expect(ready.textContent).toContain('この声に決める')
  expect(ready.className).toMatch(/\bbg-primary\b/)
  expect(screen.getByText('絞り込み 60%')).toBeTruthy()
})

it('保存している間は、そのボタンの場所に参照音声の進み具合を出す', async () => {
  // 2本目の合成で止めておく
  const held = Promise.withResolvers<void>()
  let calls = 0
  vi.mocked(apiSend).mockImplementation(async () => {
    calls++
    if (calls === 2) await held.promise
    return { relative_path: `generations/${calls}.wav`, duration: 7 }
  })
  fireEvent.click(renderAt(0.6))
  fireEvent.change(screen.getByRole('textbox', { name: '見つけた声 の名前' }), {
    target: { value: '決めた声' },
  })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'この声で作る' }))
  })
  expect(screen.getByRole('status').textContent).toContain('参照音声を作っています 1/4')
  await act(async () => held.resolve())
})
