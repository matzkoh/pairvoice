/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { Suspense } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiGet, apiSend } from '@/lib/api'

import { DictTable } from './DictTable'

// apiGet / apiSend をモックする。「静かに間違う」4点
// （from が空の行を送らない・undo がスタック・input フォーカス中は Cmd+Z を横取りしない・
// 保存失敗時に編集内容が残る）を実 API なしで確かめる。
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn(), apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderTable() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <Suspense fallback="読み込み中">
        <DictTable />
      </Suspense>
    </QueryClientProvider>,
  )
}

function seedRows() {
  vi.mocked(apiGet).mockResolvedValue({
    rows: [
      { from: 'あ', to: 'ア', memo: 'm1' },
      { from: 'い', to: 'イ', memo: 'm2' },
      { from: 'う', to: 'ウ', memo: 'm3' },
    ],
  })
}

it('保存時に from が空の行（末尾の空行）を送らない', async () => {
  seedRows()
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  renderTable()

  const saveBtn = await screen.findByRole('button', { name: '保存' })
  await act(async () => {
    saveBtn.click()
  })

  expect(apiSend).toHaveBeenCalledWith('/api/dict', 'PUT', {
    rows: [
      { from: 'あ', to: 'ア', memo: 'm1' },
      { from: 'い', to: 'イ', memo: 'm2' },
      { from: 'う', to: 'ウ', memo: 'm3' },
    ],
  })
})

it('末尾の空行に入力すると削除ボタンが付き、新しい空行が増える', async () => {
  seedRows()
  renderTable()

  await screen.findByDisplayValue('あ')
  expect(screen.getAllByRole('button', { name: '行を削除' })).toHaveLength(3)

  const inputs = within(screen.getByRole('table')).getAllByRole('textbox')
  // 末尾の空行（表の4行目、from列）。textarea（プレビュー欄）を挟まないよう
  // 空文字の input を探す。
  const emptyFromInput = inputs.find(
    (el): el is HTMLInputElement => el instanceof HTMLInputElement && el.value === '',
  )
  expect(emptyFromInput).toBeTruthy()
  if (!emptyFromInput) throw new Error('unreachable')

  await act(async () => {
    fireEvent.change(emptyFromInput, { target: { value: 'え' } })
  })

  expect(screen.getAllByRole('button', { name: '行を削除' })).toHaveLength(4)
  expect(screen.getByDisplayValue('え')).toBeTruthy()
})

it('削除は複数回 undo できる（スタック、Cmd/Ctrl+Z、フォーカスが外にあるとき）', async () => {
  seedRows()
  renderTable()

  await screen.findByDisplayValue('あ')
  expect(screen.getAllByRole('button', { name: '行を削除' })).toHaveLength(3)

  await act(async () => {
    screen.getAllByRole('button', { name: '行を削除' })[0]!.click() // 「あ」を削除
  })
  await act(async () => {
    screen.getAllByRole('button', { name: '行を削除' })[0]!.click() // 「い」を削除
  })

  expect(screen.queryByDisplayValue('あ')).toBeNull()
  expect(screen.queryByDisplayValue('い')).toBeNull()
  expect(screen.getByDisplayValue('う')).toBeTruthy()

  // フォーカスは input の外（document.body）にある。
  await act(async () => {
    fireEvent.keyDown(document, { key: 'z', ctrlKey: true })
  })
  expect(await screen.findByDisplayValue('い')).toBeTruthy()

  await act(async () => {
    fireEvent.keyDown(document, { key: 'z', ctrlKey: true })
  })
  expect(await screen.findByDisplayValue('あ')).toBeTruthy()

  // 元の並びに戻っている（1回しか戻せない実装だと2件目が復元されない）。
  const values = screen
    .getAllByRole('textbox')
    .filter((el): el is HTMLInputElement => el.tagName === 'INPUT')
    .map((el) => el.value)
    .filter((v) => v === 'あ' || v === 'い' || v === 'う')
  expect(values).toEqual(['あ', 'い', 'う'])
})

it('input にフォーカスがある間の Cmd/Ctrl+Z は横取りしない', async () => {
  seedRows()
  renderTable()

  await screen.findByDisplayValue('あ')
  await act(async () => {
    screen.getAllByRole('button', { name: '行を削除' })[0]!.click() // 「あ」を削除
  })
  expect(screen.queryByDisplayValue('あ')).toBeNull()

  const remainingInput = screen.getByDisplayValue('い')
  remainingInput.focus()
  expect(document.activeElement).toBe(remainingInput)

  await act(async () => {
    fireEvent.keyDown(remainingInput, { key: 'z', ctrlKey: true })
  })

  // アプリ側の undo は働かない（セル文字の取り消しはブラウザのネイティブ undo に任せる）。
  expect(screen.queryByDisplayValue('あ')).toBeNull()
})

it('保存に失敗しても編集内容は画面に残り、ステータス行に赤字が出る', async () => {
  seedRows()
  vi.mocked(apiSend).mockRejectedValue(new Error('boom'))
  renderTable()

  const fromInput = await screen.findByDisplayValue('あ')
  await act(async () => {
    fireEvent.change(fromInput, { target: { value: 'あ2' } })
  })

  const saveBtn = screen.getByRole('button', { name: '保存' })
  await act(async () => {
    saveBtn.click()
  })

  expect(await screen.findByText('保存に失敗しました: boom')).toBeTruthy()
  // 編集内容が画面に残っている（useOptimistic の自動ロールバックのように
  // サーバー側の値へ戻らない）。
  expect(screen.getByDisplayValue('あ2')).toBeTruthy()
  // タブごとエラー画面に差し替わっていない。
  expect(screen.getByRole('button', { name: '保存' })).toBeTruthy()
})

it('保存成功のメッセージは自動で消える', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  seedRows()
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  renderTable()

  const saveBtn = await screen.findByRole('button', { name: '保存' })
  await act(async () => {
    saveBtn.click()
  })
  expect(await screen.findByText('保存しました')).toBeTruthy()

  await act(async () => {
    vi.advanceTimersByTime(2000)
  })
  expect(screen.queryByText('保存しました')).toBeNull()
  vi.useRealTimers()
})

it('プレビューはボタンを押さずに、from が空の行を除いて送り、結果を表示する', async () => {
  seedRows()
  vi.mocked(apiSend).mockResolvedValue({ result: 'アイウ変換後' })
  renderTable()

  expect(await screen.findByText('アイウ変換後')).toBeTruthy()
  expect(apiSend).toHaveBeenCalledWith(
    '/api/dict/test',
    'POST',
    expect.objectContaining({
      rows: [
        { from: 'あ', to: 'ア' },
        { from: 'い', to: 'イ' },
        { from: 'う', to: 'ウ' },
      ],
    }),
  )
})

it('表を編集するとプレビューを取り直す', async () => {
  seedRows()
  vi.mocked(apiSend).mockResolvedValue({ result: '変換後' })
  renderTable()

  await screen.findByText('変換後')
  vi.mocked(apiSend).mockResolvedValue({ result: '編集後' })
  fireEvent.change(screen.getByDisplayValue('ア'), { target: { value: 'A' } })

  expect(await screen.findByText('編集後')).toBeTruthy()
  expect(apiSend).toHaveBeenLastCalledWith(
    '/api/dict/test',
    'POST',
    expect.objectContaining({
      rows: expect.arrayContaining([{ from: 'あ', to: 'A' }]),
    }),
  )
})

it('プレビューの失敗は例外を外に出さずインラインで表示する', async () => {
  seedRows()
  vi.mocked(apiSend).mockRejectedValue(new Error('boom'))
  renderTable()

  expect(await screen.findByText('プレビューに失敗しました: boom')).toBeTruthy()
  // タブごと消えていない（表がまだ存在する）。
  expect(screen.getByDisplayValue('あ')).toBeTruthy()
})

it('レビューから辞書に追加した行（キャッシュ書き込み）が表に出て、保存しても残る', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(['dict'], {
    rows: [
      { from: 'あ', to: 'ア', memo: 'm1' },
      { from: 'PR', to: 'ピーアール', memo: '' },
    ],
  })
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  render(
    <QueryClientProvider client={queryClient}>
      <Suspense fallback="読み込み中">
        <DictTable />
      </Suspense>
    </QueryClientProvider>,
  )

  expect(await screen.findByDisplayValue('PR')).toBeTruthy()
  await act(async () => {
    screen.getByRole('button', { name: '保存' }).click()
  })

  expect(apiSend).toHaveBeenCalledWith('/api/dict', 'PUT', {
    rows: [
      { from: 'あ', to: 'ア', memo: 'm1' },
      { from: 'PR', to: 'ピーアール', memo: '' },
    ],
  })
})
