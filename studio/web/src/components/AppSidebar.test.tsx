/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiGet, UnreachableServerError } from '@/lib/api'

import type { CorpusItem, CorpusResponse } from '../../../shared/api-types'
import { AppSidebar } from './AppSidebar'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function item(overrides: Partial<CorpusItem>): CorpusItem {
  return {
    ts: '2026-09-29 10:00:00',
    message_id: 'm',
    input: '入力',
    summary: '要約',
    verdict: null,
    ideal: null,
    archived: false,
    stale: false,
    ...overrides,
  }
}

function mockApi(items: CorpusItem[]) {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === '/health') throw new UnreachableServerError('/health', new Error('down'))
    if (path.startsWith('/corpus')) {
      const corpus: CorpusResponse = { total: items.length, items, prompt_changed_at: null }
      return corpus
    }
    throw new Error(`unexpected path in test: ${path}`)
  })
}

const PATHS = ['/review', '/prompt', '/dict', '/profiles']

function renderAt(path: string) {
  const rootRoute = createRootRoute({
    component: () => (
      <>
        <AppSidebar />
        <Outlet />
      </>
    ),
  })
  const children = PATHS.map((p) =>
    createRoute({ getParentRoute: () => rootRoute, path: p, component: () => null }),
  )
  const router = createRouter({
    routeTree: rootRoute.addChildren(children),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

it('用途別の3グループを出し、今いる画面の項目を active にする', async () => {
  mockApi([])
  renderAt('/dict')
  const link = await screen.findByRole('link', { name: '辞書' })
  expect(link.getAttribute('data-status')).toBe('active')
  for (const label of ['要約', '読み', '声'])
    expect(screen.getByRole('group', { name: label })).toBeTruthy()
})

it('レビューに未レビュー件数のバッジが付く', async () => {
  mockApi([
    item({ message_id: 'a' }),
    item({ message_id: 'b' }),
    item({ message_id: 'c', verdict: 'good' }),
  ])
  renderAt('/review')
  expect(await screen.findByLabelText('未レビュー 2 件')).toBeTruthy()
})

it('pairvoice 停止中はそう出し、ミュートは押せない', async () => {
  mockApi([])
  renderAt('/review')
  expect(await screen.findByText('pairvoice 停止')).toBeTruthy()
  expect(screen.getByRole('button', { name: /ミュート/ })).toHaveProperty('disabled', true)
})
