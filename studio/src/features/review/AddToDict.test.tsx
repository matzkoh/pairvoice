/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiSend } from '@/lib/api'
import type { DictResponse } from '@/lib/api-types'

import { AddToDict } from './AddToDict'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn(), apiSend: vi.fn() }
})

const DICT: DictResponse = { rows: [{ from: 'API', to: 'エーピーアイ', memo: '略語' }] }

beforeEach(() => {
  vi.mocked(apiGet).mockResolvedValue(DICT)
  vi.mocked(apiSend).mockImplementation(async (path: string) => {
    if (path === '/dict/test') return { result: 'プレビュー結果' }
    return { ok: true }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// 要約の要素の中で selection を選んだ状態にしてから描く
function renderAddToDict(selection: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <span id="summary">{selection}</span>
      <AddToDict summary="PR を API で出しました" summaryId="summary" />
    </QueryClientProvider>,
  )
  const range = document.createRange()
  range.selectNodeContents(document.getElementById('summary')!)
  window.getSelection()!.removeAllRanges()
  window.getSelection()!.addRange(range)
}

async function open() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /辞書に追加/ }))
  })
}

it('開くと、要約で選んでいた語が表記に入っている', async () => {
  renderAddToDict('PR')
  await open()
  expect(await screen.findByDisplayValue('PR')).toBeTruthy()
})

it('保存すると取り直した辞書に1行足して書き、結果を出して閉じる', async () => {
  renderAddToDict('PR')
  await open()
  fireEvent.change(await screen.findByLabelText('読み'), { target: { value: 'ピーアール' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
  })
  expect(apiSend).toHaveBeenCalledWith('/dict', 'PUT', {
    rows: [...DICT.rows, { from: 'PR', to: 'ピーアール', memo: '' }],
  })
  expect(await screen.findByText(/辞書に追加しました/)).toBeTruthy()
  expect(screen.queryByLabelText('読み')).toBeNull()
})

it('同じ表記があれば警告し、置き換えての保存になる', async () => {
  renderAddToDict('API')
  await open()
  expect(await screen.findByText(/登録済みです/)).toBeTruthy()
  expect(screen.getByRole('button', { name: '置き換えて保存' })).toBeTruthy()
})

it('読みが空のうちは保存できない', async () => {
  renderAddToDict('PR')
  await open()
  const save = await screen.findByRole('button', { name: '保存' })
  expect(save).toHaveProperty('disabled', true)
  fireEvent.change(screen.getByLabelText('読み'), { target: { value: '  ' } })
  expect(save).toHaveProperty('disabled', true)
  fireEvent.change(screen.getByLabelText('読み'), { target: { value: 'ピーアール' } })
  expect(save).toHaveProperty('disabled', false)
})

it('置き換えるときは既存の行のメモを引き継ぐ', async () => {
  renderAddToDict('API')
  await open()
  expect(await screen.findByDisplayValue('略語')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('読み'), { target: { value: 'エーピーアイ2' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '置き換えて保存' }))
  })
  expect(apiSend).toHaveBeenCalledWith('/dict', 'PUT', {
    rows: [{ from: 'API', to: 'エーピーアイ2', memo: '略語' }],
  })
})

it('プレビューに失敗したら理由を出し、前の結果は出さない', async () => {
  renderAddToDict('PR')
  await open()
  expect(await screen.findByText('プレビュー結果')).toBeTruthy()
  vi.mocked(apiSend).mockRejectedValue(new Error('boom'))
  fireEvent.change(screen.getByLabelText('読み'), { target: { value: 'ピーアール' } })
  expect(await screen.findByText(/プレビューできませんでした: boom/)).toBeTruthy()
  expect(screen.queryByText('プレビュー結果')).toBeNull()
})
