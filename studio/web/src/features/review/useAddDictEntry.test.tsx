/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiGet, apiSend } from '@/lib/api'

import type { DictResponse } from '../../../../shared/api-types'
import { useAddDictEntry } from './useAddDictEntry'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn(), apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function Harness() {
  const add = useAddDictEntry()
  return (
    <button type="button" onClick={() => void add({ from: 'PR', to: 'ピーアール', memo: '' })}>
      add
    </button>
  )
}

it('書く直前に取り直した辞書へ1行足して PUT し、キャッシュにも同じ内容を置く', async () => {
  const onServer: DictResponse = { rows: [{ from: 'API', to: 'エーピーアイ', memo: '' }] }
  vi.mocked(apiGet).mockResolvedValue(onServer)
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // 手元のキャッシュは古い（サーバーには API の行がある）
  queryClient.setQueryData<DictResponse>(['dict'], { rows: [] })

  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  )
  await act(async () => screen.getByRole('button', { name: 'add' }).click())

  const expected = [
    { from: 'API', to: 'エーピーアイ', memo: '' },
    { from: 'PR', to: 'ピーアール', memo: '' },
  ]
  expect(apiGet).toHaveBeenCalledWith('/dict')
  expect(apiSend).toHaveBeenCalledWith('/dict', 'PUT', { rows: expected })
  expect(queryClient.getQueryData<DictResponse>(['dict'])?.rows).toEqual(expected)
})
