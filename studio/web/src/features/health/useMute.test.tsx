/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import { useMute } from './useMute'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function Harness() {
  const { error, choose } = useMute()
  return (
    <div>
      <button type="button" onClick={() => choose(30)}>
        30分
      </button>
      <button type="button" onClick={() => choose(null)}>
        解除
      </button>
      <p data-testid="error">{error}</p>
    </div>
  )
}

function renderHarness() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  )
}

it('分数を送り、解除は minutes: null を明示して送る', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  renderHarness()
  await act(async () => screen.getByRole('button', { name: '30分' }).click())
  await act(async () => screen.getByRole('button', { name: '解除' }).click())
  expect(apiSend).toHaveBeenNthCalledWith(1, '/api/mute', 'POST', { minutes: 30 })
  expect(apiSend).toHaveBeenNthCalledWith(2, '/api/mute', 'POST', { minutes: null })
})

it('失敗しても投げ直さず、error に理由を持つ', async () => {
  vi.mocked(apiSend).mockRejectedValue(new Error('boom'))
  renderHarness()
  await act(async () => screen.getByRole('button', { name: '30分' }).click())
  expect(screen.getByTestId('error').textContent).toBe('boom')
})
