/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import type { ProfileItem } from '../../../../shared/api-types'
import { ToneEditor } from './ToneEditor'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const item: ProfileItem = {
  id: 'p-a',
  name: '元気',
  caption: '',
  source: 'design',
  created_at: '',
  tone: '元気な口調',
}

async function renderTone(onStatus = vi.fn()) {
  // 既定の口調への Link があるので、ルーターの中で描く
  const rootRoute = createRootRoute({
    component: () => <ToneEditor item={item} onStatus={onStatus} />,
  })
  const router = createRouter({ routeTree: rootRoute, history: createMemoryHistory() })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return screen.findByLabelText<HTMLTextAreaElement>('要約の口調', { selector: 'textarea' })
}

it('口調はプロファイルの PATCH で保存する', async () => {
  vi.mocked(apiSend).mockResolvedValue(item)
  const onStatus = vi.fn()
  const editor = await renderTone(onStatus)
  expect(editor.value).toBe('元気な口調')

  fireEvent.change(editor, { target: { value: '' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
  })

  // 空で保存すると既定の口調で読む
  expect(apiSend).toHaveBeenCalledWith('/profiles/p-a', 'PATCH', { tone: '' })
  expect(onStatus).toHaveBeenCalledWith({
    message: '口調を保存しました（次の読み上げから効きます）',
    isError: false,
  })
})
