/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Suspense } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiSend } from '@/lib/api'

import { PromptEditor } from './PromptEditor'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn(), apiSend: vi.fn() }
})

beforeEach(() => {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === '/prompt') return { text: '元のプロンプト' }
    if (path === '/prompt/history') return { items: [] }
    throw new Error(`unexpected path in test: ${path}`)
  })
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderEditor() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Suspense fallback={null}>
        <PromptEditor />
      </Suspense>
    </QueryClientProvider>,
  )
  return screen.findByLabelText<HTMLTextAreaElement>('プロンプト')
}

it('保存すると本文を PUT し、保存しましたと出す', async () => {
  const editor = await renderEditor()
  fireEvent.change(editor, { target: { value: '新しいプロンプト' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '保存（旧版は履歴へ）' }))
  })
  expect(apiSend).toHaveBeenCalledWith('/prompt', 'PUT', { text: '新しいプロンプト' })
  expect(await screen.findByText('保存しました')).toBeTruthy()
})

it('変更を破棄すると保存済みの本文に戻る', async () => {
  const editor = await renderEditor()
  fireEvent.change(editor, { target: { value: '書きかけ' } })
  fireEvent.click(screen.getByRole('button', { name: '変更を破棄' }))
  expect(editor.value).toBe('元のプロンプト')
})

it('保存中に打ち足した分は、保存した本文が戻ってきても消さない', async () => {
  let serverText = '元のプロンプト'
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path === '/prompt') return { text: serverText }
    if (path === '/prompt/history') return { items: [] }
    throw new Error(`unexpected path in test: ${path}`)
  })
  const saving = Promise.withResolvers<unknown>()
  vi.mocked(apiSend).mockReturnValue(saving.promise)
  const editor = await renderEditor()

  fireEvent.change(editor, { target: { value: '保存する本文' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '保存（旧版は履歴へ）' }))
  })
  fireEvent.change(editor, { target: { value: '保存する本文に打ち足した' } })
  serverText = '保存する本文'
  await act(async () => saving.resolve({ ok: true }))

  expect(await screen.findByText('保存しました')).toBeTruthy()
  expect(editor.value).toBe('保存する本文に打ち足した')
})
