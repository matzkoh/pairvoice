/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSendForm } from '@/lib/api'
import type { CurateResponse } from '@/lib/api-types'

import { CurateProfile } from './CurateProfile'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSendForm: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const RESPONSE: CurateResponse = {
  profile: {
    id: 'p-c',
    name: '対談',
    caption: '',
    source: 'curate',
    created_at: '1',
    tone: '',
    members: [],
    order: 'random',
  },
  report: {
    files: [{ name: '対談.m4a', seconds: 3600 }],
    truncated: false,
    speakers: [
      { speaker: 0, seconds: 1800, chosen: true },
      { speaker: 1, seconds: 600, chosen: false },
    ],
    candidates: 120,
    seconds: 19.5,
    pieces: [{ file: '対談.m4a', start: 75.2, end: 81.0, snr_db: 21.3 }],
  },
}

function renderForm() {
  const onCreated = vi.fn()
  const onStatus = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CurateProfile onStatus={onStatus} onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return { onCreated, onStatus }
}

function pick(...names: string[]) {
  const files = names.map((n) => new File(['x'], n, { type: 'audio/mp4' }))
  fireEvent.change(screen.getByLabelText('素材のファイル'), { target: { files } })
}

it('選んだファイルをすべて送り、使った区間を見せてから開ける', async () => {
  vi.mocked(apiSendForm).mockResolvedValue(RESPONSE)
  const { onCreated } = renderForm()

  pick('対談.m4a', '配信.mp4')
  // 名前は最初のファイルから拡張子を除いて埋める
  expect(screen.getByLabelText<HTMLInputElement>('名前').value).toBe('対談')
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'プロファイルを作る' }))
  })

  const [path, form] = vi.mocked(apiSendForm).mock.calls[0] ?? []
  expect(path).toBe('/profiles/curate?name=%E5%AF%BE%E8%AB%87&caption=')
  const sent = form?.getAll('files').map((f) => (f instanceof File ? f.name : f))
  expect(sent).toEqual(['対談.m4a', '配信.mp4'])
  // 内訳を見せてから、開くのは押したとき
  expect(onCreated).not.toHaveBeenCalled()
  expect(screen.getByText(/1 区間（候補 120 区間）から、19.5 秒の声/)).toBeTruthy()
  expect(screen.getByText(/全体の 75%/)).toBeTruthy()
  expect(screen.getByText(/対談.m4a 1:15〜1:21・S\/N 21.3 dB/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '「対談」を開く' }))
  expect(onCreated).toHaveBeenCalledWith('p-c')
})

it('作れなければ理由を知らせ、選んだファイルを残す', async () => {
  vi.mocked(apiSendForm).mockRejectedValue(new Error('きれいな声が 3.0 秒しか見つかりません'))
  const { onStatus, onCreated } = renderForm()

  pick('短い.wav')
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'プロファイルを作る' }))
  })

  expect(onStatus).toHaveBeenCalledWith({
    message: '作れませんでした: きれいな声が 3.0 秒しか見つかりません',
    isError: true,
  })
  expect(onCreated).not.toHaveBeenCalled()
  expect(screen.getByText(/短い.wav/)).toBeTruthy()
})
