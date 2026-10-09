/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { apiGet, apiSend } from '@/lib/api'

import { VoiceWorkbench } from './VoiceWorkbench'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiGet: vi.fn(), apiSend: vi.fn() }
})

beforeEach(() => {
  vi.mocked(apiSend).mockImplementation(async (path: string) => {
    if (path === '/api/speak') return { relative_path: 'generations/x.wav', duration: 1.5 }
    return { ok: true }
  })
  vi.mocked(apiGet).mockResolvedValue({ items: [] })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

type Props = ComponentProps<typeof VoiceWorkbench>

const onPlay = vi.fn()
const onStatus = vi.fn()

const BASE: Props = {
  profileId: 'p-a',
  adoptedCaption: '声A',
  sampler: undefined,
  pairvoiceDown: false,
  outdated: false,
  onPlay,
  onStatus,
}

function renderWorkbench(props: Partial<Props> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <VoiceWorkbench {...BASE} {...props} />
    </QueryClientProvider>,
  )
  const rerender = (next: Partial<Props>) =>
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <VoiceWorkbench {...BASE} {...props} {...next} />
      </QueryClientProvider>,
    )
  return { rerender }
}

const caption = () => screen.getByLabelText<HTMLTextAreaElement>('caption')

async function generate() {
  await act(async () => {
    screen.getByRole('button', { name: /生成/ }).click()
  })
}

it('pairvoice が止まっていれば生成できず、理由を出す', () => {
  renderWorkbench({ pairvoiceDown: true })
  expect(screen.getByRole('button', { name: /生成/ })).toHaveProperty('disabled', true)
  expect(screen.getByText(/pairvoice が止まっている/)).toBeTruthy()
})

it('pairvoice が古ければ警告する', () => {
  renderWorkbench({ outdated: true })
  expect(screen.getByText(/pairvoice が古く/)).toBeTruthy()
})

it('入力欄の caption を、開いているプロファイルの声で試聴し、プロファイルは書かない', async () => {
  renderWorkbench()
  fireEvent.change(caption(), { target: { value: '声B' } })
  await generate()
  expect(apiSend).toHaveBeenCalledWith(
    '/api/speak',
    'POST',
    expect.objectContaining({ caption: '声B', profile_id: 'p-a' }),
  )
  expect(apiSend).not.toHaveBeenCalledWith('/api/profiles/p-a', 'PATCH', expect.anything())
  expect(await screen.findByRole('button', { name: /テイク 1 を再生/ })).toBeTruthy()
})

it('caption を空にして試聴し、空のまま採用できる', async () => {
  renderWorkbench()
  fireEvent.change(caption(), { target: { value: '' } })
  await generate()
  expect(apiSend).toHaveBeenCalledWith(
    '/api/speak',
    'POST',
    expect.objectContaining({ caption: '' }),
  )
  expect(await screen.findByText(/caption 変更あり: （なし）/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'この caption を採用' }))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '採用してよいですか' }))
  })
  expect(apiSend).toHaveBeenCalledWith('/api/profiles/p-a', 'PATCH', { caption: '' })
})

it('⌘↵ でも生成する', async () => {
  renderWorkbench()
  await act(async () => {
    fireEvent.keyDown(caption(), { key: 'Enter', metaKey: true })
  })
  expect(apiSend).toHaveBeenCalledWith('/api/speak', 'POST', expect.anything())
})

it('入力欄をさらに書き換えても、採用するのはテイクを作ったときの caption', async () => {
  renderWorkbench()
  fireEvent.change(caption(), { target: { value: '声B' } })
  await generate()
  fireEvent.change(caption(), { target: { value: '声C' } })
  fireEvent.click(await screen.findByRole('button', { name: 'この caption を採用' }))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '採用してよいですか' }))
  })
  expect(apiSend).toHaveBeenCalledWith('/api/profiles/p-a', 'PATCH', { caption: '声B' })
})

it('保存済みの caption が変わったら、書き換えていない入力欄は追従する', () => {
  const { rerender } = renderWorkbench()
  rerender({ adoptedCaption: '声Z' })
  expect(caption().value).toBe('声Z')
})

it('サンプラーの値が不正なら生成せず、理由を出す', async () => {
  renderWorkbench()
  fireEvent.change(screen.getByLabelText('Seconds'), { target: { value: 'あ' } })
  await generate()
  expect(apiSend).not.toHaveBeenCalledWith('/api/speak', 'POST', expect.anything())
  expect(screen.getByText(/seconds/)).toBeTruthy()
})

it('合成が失敗したらそのテイクに理由を出し、残りの待機テイクは捨てる', async () => {
  vi.mocked(apiSend).mockResolvedValue({ error: 'model_load_failed' })
  renderWorkbench()
  fireEvent.change(screen.getByLabelText(/候補数/), { target: { value: '3' } })
  await generate()
  expect(await screen.findByText(/model_load_failed/)).toBeTruthy()
  expect(screen.getAllByRole('button', { name: /テイク \d+ を再生/ })).toHaveLength(1)
  expect(apiSend).toHaveBeenCalledTimes(1)
})

function mockSpeakPaths() {
  let n = 0
  vi.mocked(apiSend).mockImplementation(async (path: string) => {
    if (path === '/api/speak') return { relative_path: `generations/t${++n}.wav`, duration: 1.5 }
    return { ok: true }
  })
}

it('1回の生成では最初に鳴らせるようになったテイクだけを自動で再生する', async () => {
  mockSpeakPaths()
  renderWorkbench()
  fireEvent.change(screen.getByLabelText(/候補数/), { target: { value: '2' } })
  await generate()
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'クリア' })).toHaveProperty('disabled', false),
  )
  expect(apiSend).toHaveBeenCalledTimes(2)
  expect(onPlay).toHaveBeenCalledTimes(1)
  expect(onPlay).toHaveBeenCalledWith(
    expect.stringContaining(encodeURIComponent('generations/t1.wav')),
  )
})

it('再生成が成功したら、そのテイクを再生する', async () => {
  mockSpeakPaths()
  renderWorkbench()
  await generate()
  await waitFor(() => expect(onPlay).toHaveBeenCalledTimes(1))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'この seed で再生成' }))
  })
  await waitFor(() => expect(onPlay).toHaveBeenCalledTimes(2))
  expect(onPlay).toHaveBeenLastCalledWith(
    expect.stringContaining(encodeURIComponent('generations/t2.wav')),
  )
})

it('caption の履歴を開いている間は ⌘↵ で生成しない', async () => {
  renderWorkbench()
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'caption の履歴' }))
  })
  const sheet = await screen.findByRole('dialog')
  await act(async () => {
    fireEvent.keyDown(sheet, { key: 'Enter', metaKey: true })
  })
  expect(apiSend).not.toHaveBeenCalledWith('/api/speak', 'POST', expect.anything())
})

it('pairvoice が止まったら再生成できない', async () => {
  const { rerender } = renderWorkbench()
  await generate()
  const retry = await screen.findByRole('button', { name: 'この seed で再生成' })
  await waitFor(() => expect(retry).toHaveProperty('disabled', false))
  rerender({ pairvoiceDown: true })
  expect(retry).toHaveProperty('disabled', true)
})
