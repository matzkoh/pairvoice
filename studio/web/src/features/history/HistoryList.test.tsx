/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { Suspense } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiGet, apiSend } from '@/lib/api'

import { HistoryList } from './HistoryList'

// apiGet / apiSend をモックして、復元の失敗が画面ごと消える壊れ方をしないことを
// 確かめる。startTransition 内の throw は素通しにすると
// 最も近い error boundary まで再 throw され、ここには boundary が無いのでテストが
// クラッシュする形で失敗を検知できる。
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn(), apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderHistoryList(onError: (message: string) => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <Suspense fallback="読み込み中">
        <HistoryList source={{ path: '/prompt', key: ['prompt'] }} onError={onError} />
      </Suspense>
    </QueryClientProvider>,
  )
}

it('復元の失敗は onError に渡り、例外は外へ出ない', async () => {
  vi.mocked(apiGet).mockResolvedValue({
    items: [{ name: 'prompt-2026-01-01T00-00-00-000Z.txt', ts: '2026-01-01T00-00-00-000Z' }],
  })
  vi.mocked(apiSend).mockRejectedValue(new Error('boom'))
  const onError = vi.fn()

  renderHistoryList(onError)

  const btn = await screen.findByRole('button', { name: '復元' })
  await act(async () => {
    btn.click()
  })

  await waitFor(() => {
    expect(onError).toHaveBeenCalledWith('boom')
  })
  // タブが root の errorComponent に差し替わっていれば、この復元ボタンは
  // もう DOM に無い。
  expect(screen.getByRole('button', { name: '復元' })).toBe(btn)
})

it('復元をやり直して成功したら、前の失敗を消す', async () => {
  vi.mocked(apiGet).mockResolvedValue({
    items: [{ name: 'prompt-2026-01-01T00-00-00-000Z.txt', ts: '2026-01-01T00-00-00-000Z' }],
  })
  vi.mocked(apiSend).mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce({})
  const onError = vi.fn()

  renderHistoryList(onError)

  const btn = await screen.findByRole('button', { name: '復元' })
  await act(async () => {
    btn.click()
  })
  await waitFor(() => {
    expect(onError).toHaveBeenLastCalledWith('boom')
  })
  await act(async () => {
    btn.click()
  })
  await waitFor(() => {
    expect(onError).toHaveBeenLastCalledWith('')
  })
})
