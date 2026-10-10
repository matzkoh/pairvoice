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
import type { CorpusCounts } from '@/lib/api-types'

import { AppSidebar } from './AppSidebar'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function mockApi(unreviewed = 0) {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === '/health') throw new UnreachableServerError('/health', new Error('down'))
    if (path === '/corpus/counts') {
      const counts: CorpusCounts = { all: unreviewed, unreviewed, bad: 0, archived: 0, stale: 0 }
      return counts
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
  mockApi()
  renderAt('/dict')
  const link = await screen.findByRole('link', { name: '辞書' })
  expect(link.getAttribute('data-status')).toBe('active')
  for (const label of ['要約', '読み', '声'])
    expect(screen.getByRole('group', { name: label })).toBeTruthy()
})

it('レビューに未レビュー件数のバッジが付く', async () => {
  mockApi(2)
  renderAt('/review')
  expect(await screen.findByLabelText('未レビュー 2 件')).toBeTruthy()
})

it('pairvoice 停止中はそう出し、ミュートは押せない', async () => {
  mockApi()
  renderAt('/review')
  expect(await screen.findByText('pairvoice 停止')).toBeTruthy()
  expect(screen.getByRole('button', { name: /ミュート/ })).toHaveProperty('disabled', true)
})
