/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiSend } from '@/lib/api'

import type { CorpusItem, CorpusResponse, StudioHealth } from '../../../shared/api-types'
import { Route as rootRoute } from './__root'
import { Route as reviewRoute } from './review'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn(), apiSend: vi.fn() }
})

function item(overrides: Partial<CorpusItem>): CorpusItem {
  return {
    ts: '2026-09-29 10:00:00',
    message_id: 'm',
    input: '入力',
    summary: '要約',
    audio_path: 'x.wav',
    verdict: null,
    ideal: null,
    archived: false,
    stale: false,
    ...overrides,
  }
}

const ITEMS = [
  item({ message_id: 'a', summary: '一つ目' }),
  item({ message_id: 'b', summary: '二つ目' }),
  item({ message_id: 'c', summary: '三つ目', verdict: 'good' }),
  item({ message_id: 'd', summary: '四つ目', verdict: 'bad', ideal: '' }),
]
const HEALTH: StudioHealth = { pairvoice: null }

beforeEach(() => {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === '/api/health') return HEALTH
    if (path.startsWith('/api/corpus')) {
      const corpus: CorpusResponse = { total: ITEMS.length, items: ITEMS, prompt_changed_at: null }
      return corpus
    }
    throw new Error(`unexpected path in test: ${path}`)
  })
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

async function renderReview() {
  const router = createRouter({
    routeTree: rootRoute.addChildren([reviewRoute]),
    history: createMemoryHistory({ initialEntries: ['/review'] }),
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  await screen.findByText('一つ目')
}

async function press(key: string, target: Element = document.body) {
  await act(async () => {
    fireEvent.keyDown(target, { key })
  })
}

function selectedRow() {
  return document.querySelector('[data-selected]')
}

it('既定は未レビューで、評価済みの行は出ない', async () => {
  await renderReview()
  expect(screen.getByText('二つ目')).toBeTruthy()
  expect(screen.queryByText('三つ目')).toBeNull()
})

it('j で先頭を選んで展開し、Enter で畳む', async () => {
  await renderReview()
  await press('j')
  expect(selectedRow()?.textContent).toContain('一つ目')
  expect(screen.getByText('生成元テキスト')).toBeTruthy()
  await press('Enter')
  expect(screen.queryByText('生成元テキスト')).toBeNull()
})

it('1 で 👍 を投稿し、未レビューから消えた行の次が選ばれる', async () => {
  await renderReview()
  await press('j')
  await press('1')
  expect(apiSend).toHaveBeenCalledWith('/api/reviews', 'POST', {
    message_id: 'a',
    verdict: 'good',
    ideal: '',
  })
  await waitFor(() => expect(screen.queryByText('一つ目')).toBeNull())
  expect(selectedRow()?.textContent).toContain('二つ目')
  // 続けて 1 を押せば、選び直さずに次の行も片付く
  await press('1')
  expect(apiSend).toHaveBeenLastCalledWith('/api/reviews', 'POST', {
    message_id: 'b',
    verdict: 'good',
    ideal: '',
  })
})

it('展開した行の 👍 をクリックしても、未レビューから消えた行の次が選ばれる', async () => {
  await renderReview()
  await press('j')
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '良い' }))
  })
  await waitFor(() => expect(screen.queryByText('一つ目')).toBeNull())
  expect(selectedRow()?.textContent).toContain('二つ目')
})

it('入力欄にフォーカスがあるときは 1 を押しても投稿しない', async () => {
  await renderReview()
  await press('j')
  const searchbox = screen.getByRole('searchbox', { name: 'テキスト検索' })
  for (const key of ['1', '2', 'e', 'j']) await press(key, searchbox)
  expect(apiSend).not.toHaveBeenCalled()
  expect(selectedRow()?.textContent).toContain('一つ目')
})

it('理想の出力を打っている間は 1 / 2 / e / j が効かない', async () => {
  await renderReview()
  await act(async () => {
    screen.getByRole('button', { name: /👎/ }).click()
  })
  await press('j')
  const ideal = screen.getByLabelText('理想の出力')
  for (const key of ['1', '2', 'e', 'j']) await press(key, ideal)
  expect(apiSend).not.toHaveBeenCalled()
  expect(selectedRow()?.textContent).toContain('四つ目')
})

it('行を選んでいないときの Space はページに返す（preventDefault しない）', async () => {
  await renderReview()
  let notPrevented = false
  await act(async () => {
    notPrevented = fireEvent.keyDown(document.body, { key: ' ' })
  })
  expect(notPrevented).toBe(true)
})

it('Space で選択中の行を再生する', async () => {
  await renderReview()
  await press('j')
  await press(' ')
  const audio = document.querySelector('audio')!
  expect(audio.src).toContain('/api/audio/a')
})

it('e で選択中の行をアーカイブする', async () => {
  await renderReview()
  await press('j')
  await press('e')
  expect(apiSend).toHaveBeenCalledWith('/api/archives', 'POST', { message_id: 'a', archived: true })
})

it('絞り込みを「すべて」にすると評価済みの行も出る', async () => {
  await renderReview()
  await act(async () => {
    screen.getByRole('button', { name: /すべて/ }).click()
  })
  expect(await screen.findByText('三つ目')).toBeTruthy()
})

it('「…」メニューを開いている間は 1 を押しても投稿しない', async () => {
  await renderReview()
  await press('j')
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'その他の操作' }))
  })
  await screen.findByRole('menu')
  await press('1', document.activeElement!)
  expect(apiSend).not.toHaveBeenCalled()
})

it('辞書に追加を開いている間は 1 / j が行に効かず、フォームも閉じない', async () => {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === '/api/health') return HEALTH
    if (path === '/api/dict') return { rows: [] }
    if (path.startsWith('/api/corpus')) {
      const corpus: CorpusResponse = { total: ITEMS.length, items: ITEMS, prompt_changed_at: null }
      return corpus
    }
    throw new Error(`unexpected path in test: ${path}`)
  })
  await renderReview()
  await press('j')
  // 実際のマウス操作と同じく detail: 1 で押す。トリガーのフォーカスが外れると閉じてしまう
  const trigger = screen.getByRole('button', { name: /辞書に追加/ })
  trigger.focus()
  await act(async () => {
    fireEvent.click(trigger, { detail: 1 })
  })
  const popover = await screen.findByRole('dialog')
  for (const key of ['1', 'j', 'e']) await press(key, popover)
  expect(apiSend).not.toHaveBeenCalledWith('/api/reviews', 'POST', expect.anything())
  expect(apiSend).not.toHaveBeenCalledWith('/api/archives', 'POST', expect.anything())
  expect(selectedRow()?.textContent).toContain('一つ目')
  expect(popover.querySelector('#add-dict-to')).toBeTruthy()
})

it('投稿に失敗した行の理由は、次の行を操作した後も残る', async () => {
  vi.mocked(apiSend).mockRejectedValueOnce(new Error('boom'))
  await renderReview()
  await press('j')
  await press('1')
  expect(await screen.findByText('判定の投稿に失敗しました: boom')).toBeTruthy()
  expect(selectedRow()?.textContent).toContain('二つ目')
  await press('1')
  expect(apiSend).toHaveBeenLastCalledWith('/api/reviews', 'POST', {
    message_id: 'b',
    verdict: 'good',
    ideal: '',
  })
  await waitFor(() => expect(screen.queryByText('二つ目')).toBeNull())
  const failed = screen.getByText('一つ目').closest('li')!
  expect(failed.textContent).toContain('判定の投稿に失敗しました: boom')
})

it('投票で選択が次の行へ移ると、その行を画面内へスクロールする', async () => {
  const scrolled: string[] = []
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolled.push(this.textContent ?? '')
  }
  try {
    await renderReview()
    await press('j')
    await press('1')
    await waitFor(() => expect(scrolled.at(-1)).toContain('二つ目'))
  } finally {
    // @ts-expect-error jsdom には無いので元に戻す
    delete Element.prototype.scrollIntoView
  }
})
