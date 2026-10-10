/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiSend, UnreachableServerError } from '@/lib/api'
import type { CorpusResponse, DictResponse } from '@/lib/api-types'

import { routeTree } from '../router'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn(), apiSend: vi.fn() }
})

beforeEach(() => {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === '/health') throw new UnreachableServerError('/health', new Error('down'))
    if (path.startsWith('/corpus')) {
      const corpus: CorpusResponse = { total: 0, items: [], prompt_changed_at: null }
      return corpus
    }
    if (path === '/dict') {
      const dict: DictResponse = { rows: [] }
      return dict
    }
    throw new Error(`unexpected path in test: ${path}`)
  })
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderApp() {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ['/review'] }),
  })
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  await waitFor(() => expect(router.state.location.pathname).toBe('/review'))
  return router
}

async function openPalette() {
  await act(async () => {
    fireEvent.keyDown(document.body, { key: 'k', metaKey: true })
  })
  return screen.findByRole('combobox', { name: 'コマンドを検索' })
}

it('⌘K で開き、絞り込んで Enter で画面を移る', async () => {
  const router = await renderApp()
  const input = await openPalette()
  fireEvent.change(input, { target: { value: '辞書' } })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' })
  })
  await waitFor(() => expect(router.state.location.pathname).toBe('/dict'))
})

it('↓ で選択を動かしてミュートを実行できる', async () => {
  await renderApp()
  const input = await openPalette()
  fireEvent.change(input, { target: { value: 'ミュート' } })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'ArrowDown' })
  })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' })
  })
  await waitFor(() => expect(apiSend).toHaveBeenCalledWith('/mute', 'POST', { minutes: 60 }))
})

it('一致しなければそう出す', async () => {
  await renderApp()
  const input = await openPalette()
  fireEvent.change(input, { target: { value: 'zzz' } })
  expect(screen.getByText('見つかりません')).toBeTruthy()
})

it('ミュートに失敗したら理由を出す', async () => {
  vi.mocked(apiSend).mockRejectedValueOnce(new Error('pairvoice に繋がりません'))
  await renderApp()
  const input = await openPalette()
  fireEvent.change(input, { target: { value: '30分' } })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' })
  })
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('pairvoice に繋がりません')
})

it('画面を移るコマンドの Enter は、パレットを開く前にフォーカスしていたボタンを押さない', async () => {
  const router = await renderApp()
  const outside = document.createElement('button')
  const onOutsideClick = vi.fn()
  outside.addEventListener('click', onOutsideClick)
  document.body.append(outside)
  const seenByPage = vi.fn()
  try {
    outside.focus()
    await act(async () => {
      fireEvent.keyDown(outside, { key: 'k', metaKey: true })
    })
    const input = await screen.findByRole('combobox', { name: 'コマンドを検索' })
    fireEvent.change(input, { target: { value: '辞書' } })
    document.addEventListener('keydown', seenByPage)
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' })
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/dict'))
    await waitFor(() => expect(screen.queryByRole('combobox')).toBeNull())
    expect(seenByPage).not.toHaveBeenCalled()
    expect(document.activeElement).not.toBe(outside)
    expect(onOutsideClick).not.toHaveBeenCalled()
  } finally {
    document.removeEventListener('keydown', seenByPage)
    outside.remove()
  }
})

it('IME の変換を確定する Enter ではコマンドを実行しない', async () => {
  const router = await renderApp()
  const input = await openPalette()
  fireEvent.change(input, { target: { value: '辞書' } })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })
  })
  expect(router.state.location.pathname).toBe('/review')
  expect(screen.getByRole('combobox', { name: 'コマンドを検索' })).toBeTruthy()
})
