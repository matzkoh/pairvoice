/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { apiGet, UnreachableServerError } from '@/lib/api'
import type { CorpusResponse, ProfilesResponse } from '@/lib/api-types'

import { routeTree } from '../router'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn() }
})

beforeEach(() => {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === '/health') throw new UnreachableServerError('/health', new Error('down'))
    if (path === '/profiles') {
      const profiles: ProfilesResponse = { items: [], active: null }
      return profiles
    }
    if (path.startsWith('/corpus')) {
      const corpus: CorpusResponse = { total: 0, items: [], prompt_changed_at: null }
      return corpus
    }
    throw new Error(`unexpected path in test: ${path}`)
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

it.each([
  ['/', '/review'],
  ['/voice', '/profiles'],
  ['/caption', '/profiles'],
  ['/playground', '/profiles'],
])('%s は %s に転送する', async (from, to) => {
  const router = renderAt(from)
  await waitFor(() => expect(router.state.location.pathname).toBe(to))
})
