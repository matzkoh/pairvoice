/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import type { CorpusItem, CorpusResponse } from '../../../../shared/api-types'
import { CORPUS_COUNTS_KEY } from './queries'
import { useReviewActions } from './useReviewActions'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function item(overrides: Partial<CorpusItem>): CorpusItem {
  return {
    ts: '2026-09-29 10:00:00',
    message_id: 'm1',
    input: '入力',
    summary: '要約',
    verdict: null,
    ideal: null,
    archived: false,
    stale: false,
    ...overrides,
  }
}

function setup(target: CorpusItem) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const corpus: CorpusResponse = { total: 1, items: [target], prompt_changed_at: null }
  queryClient.setQueryData(['corpus'], corpus)
  function Harness() {
    const actions = useReviewActions()
    return (
      <div>
        <button type="button" onClick={() => actions.vote(target, 'good')}>
          good
        </button>
        <button type="button" onClick={() => actions.vote(target, 'bad')}>
          bad
        </button>
        <button type="button" onClick={() => actions.toggleArchive(target)}>
          archive
        </button>
        <button type="button" onClick={() => actions.bulkArchive([target])}>
          bulk
        </button>
        <p data-testid="error">{actions.errors['m1'] ?? ''}</p>
      </div>
    )
  }
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  )
  const current = () => queryClient.getQueryData<CorpusResponse>(['corpus'])!.items[0]!
  return { current, queryClient }
}

it('投票とアーカイブのあとは、サイドバーの件数を取り直させる', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const { queryClient } = setup(item({}))
  const counts = { all: 1, unreviewed: 1, bad: 0, archived: 0, stale: 0 }
  for (const name of ['good', 'archive']) {
    queryClient.setQueryData(CORPUS_COUNTS_KEY, counts)
    await act(async () => screen.getByRole('button', { name }).click())
    expect(queryClient.getQueryState(CORPUS_COUNTS_KEY)?.isInvalidated).toBe(true)
  }
})

it('同じ判定をもう一度押すと取り消しになる', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const { current } = setup(item({ verdict: 'good' }))
  await act(async () => screen.getByRole('button', { name: 'good' }).click())
  expect(apiSend).toHaveBeenCalledWith('/reviews', 'POST', {
    message_id: 'm1',
    verdict: 'none',
    ideal: '',
  })
  expect(current().verdict).toBeNull()
})

it('投票を投稿し、キャッシュの行を書き換える', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const { current } = setup(item({}))
  await act(async () => screen.getByRole('button', { name: 'good' }).click())
  expect(apiSend).toHaveBeenCalledWith('/reviews', 'POST', {
    message_id: 'm1',
    verdict: 'good',
    ideal: '',
  })
  expect(current().verdict).toBe('good')
})

it('👎 は既存の理想の出力を持ち越す', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  setup(item({ ideal: 'こう読んで' }))
  await act(async () => screen.getByRole('button', { name: 'bad' }).click())
  expect(apiSend).toHaveBeenCalledWith('/reviews', 'POST', {
    message_id: 'm1',
    verdict: 'bad',
    ideal: 'こう読んで',
  })
})

it('旧プロンプトの行には投票しない（いまのプロンプトの挙動ではなく、判定しても手がかりにならない）', async () => {
  setup(item({ stale: true }))
  await act(async () => screen.getByRole('button', { name: 'good' }).click())
  expect(apiSend).not.toHaveBeenCalled()
})

it('アーカイブを切り替えてキャッシュに反映する', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const { current } = setup(item({}))
  await act(async () => screen.getByRole('button', { name: 'archive' }).click())
  expect(apiSend).toHaveBeenCalledWith('/archives', 'POST', {
    message_id: 'm1',
    archived: true,
  })
  expect(current().archived).toBe(true)
})

it('一括アーカイブは message_ids をまとめて送る', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const { current } = setup(item({ verdict: 'good' }))
  await act(async () => screen.getByRole('button', { name: 'bulk' }).click())
  expect(apiSend).toHaveBeenCalledWith('/archives/bulk', 'POST', {
    message_ids: ['m1'],
    archived: true,
  })
  expect(current().archived).toBe(true)
})

it('失敗しても投げ直さず、行の ID 付きで理由を持つ', async () => {
  vi.mocked(apiSend).mockRejectedValue(new Error('boom'))
  setup(item({}))
  await act(async () => screen.getByRole('button', { name: 'good' }).click())
  expect(screen.getByTestId('error').textContent).toBe('判定の投稿に失敗しました: boom')
})

it('操作後の行の姿を返す（選択の追従に使う）。旧プロンプトの行への投票は null', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const results: unknown[] = []
  function Harness() {
    const actions = useReviewActions()
    return (
      <button
        type="button"
        onClick={() => {
          results.push(
            actions.vote(item({ message_id: 'a' }), 'good'),
            actions.vote(item({ message_id: 'b', verdict: 'bad', ideal: 'こう' }), 'bad'),
            actions.vote(item({ message_id: 'c', verdict: 'good' }), 'bad'),
            actions.vote(item({ message_id: 'd', stale: true }), 'good'),
            actions.toggleArchive(item({ message_id: 'e' })),
          )
        }}
      >
        run
      </button>
    )
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness />
    </QueryClientProvider>,
  )
  await act(async () => screen.getByRole('button', { name: 'run' }).click())
  expect(results).toMatchObject([
    { verdict: 'good', ideal: '' },
    { verdict: null, ideal: null },
    { verdict: 'bad', ideal: '' },
    null,
    { archived: true },
  ])
})

it('同じ行の同じ種類の投稿が返る前の操作は受け付けない（連打で二重に投稿しない）', async () => {
  const resolvers: ((value: unknown) => void)[] = []
  vi.mocked(apiSend).mockImplementation(() => new Promise((r) => resolvers.push(r)))
  setup(item({}))
  await act(async () => screen.getByRole('button', { name: 'good' }).click())
  await act(async () => screen.getByRole('button', { name: 'good' }).click())
  expect(apiSend).toHaveBeenCalledTimes(1)
  await act(async () => resolvers[0]?.({ ok: true }))
  await act(async () => screen.getByRole('button', { name: 'good' }).click())
  expect(apiSend).toHaveBeenCalledTimes(2)
})

it('投票の投稿中でも、同じ行のアーカイブは受け付ける（別の種類の操作は止めない）', async () => {
  vi.mocked(apiSend).mockImplementation(() => new Promise(() => {}))
  setup(item({}))
  await act(async () => screen.getByRole('button', { name: 'good' }).click())
  await act(async () => screen.getByRole('button', { name: 'archive' }).click())
  expect(apiSend).toHaveBeenCalledTimes(2)
  expect(apiSend).toHaveBeenLastCalledWith('/archives', 'POST', {
    message_id: 'm1',
    archived: true,
  })
})

it('送ったかどうかを返す。判定の投稿中の理想の出力は送らず false、アーカイブは null', async () => {
  vi.mocked(apiSend).mockImplementation(() => new Promise(() => {}))
  const results: unknown[] = []
  const bad = item({ verdict: 'bad', ideal: '' })
  function Harness() {
    const actions = useReviewActions()
    return (
      <button
        type="button"
        onClick={() => {
          results.push(
            actions.saveIdeal(bad, 'こう'),
            actions.saveIdeal(bad, 'こう読んで'),
            actions.vote(bad, 'good'),
            actions.toggleArchive(bad),
            actions.toggleArchive(bad),
          )
        }}
      >
        run
      </button>
    )
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness />
    </QueryClientProvider>,
  )
  await act(async () => screen.getByRole('button', { name: 'run' }).click())
  expect(results).toMatchObject([true, false, null, { archived: true }, null])
  expect(apiSend).toHaveBeenCalledTimes(2)
})
