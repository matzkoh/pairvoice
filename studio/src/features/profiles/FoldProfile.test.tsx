/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { ApiError, apiGet, apiSendForm } from '@/lib/api'
import type { FoldJob } from '@/lib/api-types'

import { FoldProfile, POLL_MS } from './FoldProfile'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSendForm: vi.fn(), apiGet: vi.fn() }
})

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

function job(overrides: Partial<FoldJob>): FoldJob {
  return {
    job_id: 'job1',
    status: 'running',
    stage: 'clean',
    progress: 0.4,
    profile: null,
    report: null,
    error: null,
    detail: null,
    ...overrides,
  }
}

const DONE = job({
  status: 'done',
  stage: 'save',
  progress: 1,
  profile: {
    id: 'p-f',
    name: '配信',
    caption: '',
    source: 'curate',
    created_at: '1',
    tone: '',
    members: [],
    order: 'random',
    folded: false,
  },
  report: { speech_seconds: 1422, pieces: 319, blocks: 55, pooled_tokens: 8863, tokens: 180 },
})

// URL の ?fold= は親が持つので、ここでは渡された ID をそのまま次の描画に渡す
function renderForm(initial?: string) {
  const onCreated = vi.fn()
  const onStatus = vi.fn()
  const client = new QueryClient()
  let jobId = initial
  const view = () => (
    <QueryClientProvider client={client}>
      <FoldProfile
        jobId={jobId}
        onJobChange={(next) => {
          jobId = next
          rerender(view())
        }}
        onStatus={onStatus}
        onCreated={onCreated}
      />
    </QueryClientProvider>
  )
  const { rerender } = render(view())
  return { onCreated, onStatus }
}

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

it('starts a fold job, follows its progress and opens the voice', async () => {
  vi.mocked(apiSendForm).mockResolvedValue(job({ stage: 'upload', progress: 1 }))
  vi.mocked(apiGet)
    .mockResolvedValueOnce(job({ stage: 'clean', progress: 0.4 }))
    .mockResolvedValueOnce(DONE)
  const { onCreated, onStatus } = renderForm()

  const files = [new File(['x'], '雑談.m4a', { type: 'audio/mp4' })]
  fireEvent.change(screen.getByLabelText('素材のファイル'), { target: { files } })
  expect(screen.getByLabelText('名前')).toHaveProperty('value', '雑談')
  fireEvent.click(screen.getByRole('button', { name: 'プロファイルを作る' }))
  await flush()

  const [path, form] = vi.mocked(apiSendForm).mock.calls[0]!
  expect(path).toBe('/profiles/fold?name=%E9%9B%91%E8%AB%87&caption=')
  expect(form.getAll('files')).toHaveLength(1)
  expect(screen.getByText('雑音と響きを除いています')).toBeTruthy()
  // 走っている間は、同じ素材をもう一度送らない
  expect(screen.getByRole('button', { name: 'プロファイルを作る' })).toHaveProperty(
    'disabled',
    true,
  )

  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MS)
  })

  expect(onStatus).toHaveBeenLastCalledWith({ message: '「配信」を作りました', isError: false })
  expect(
    screen.getByText(/本人の声 24 分（319 区間）を、180 トークンに畳み込みました/),
  ).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '「配信」を開く' }))
  expect(onCreated).toHaveBeenCalledWith('p-f')
})

it('shows why the job failed', async () => {
  vi.mocked(apiGet).mockResolvedValue(
    job({ status: 'failed', error: 'too_little_speech', detail: '本人の声が短すぎます' }),
  )
  const { onStatus } = renderForm('job1')
  await flush()

  expect(onStatus).toHaveBeenCalledWith({
    message: '作れませんでした: 本人の声が短すぎます',
    isError: true,
  })
})

it('forgets a job the server no longer has', async () => {
  vi.mocked(apiGet).mockRejectedValue(new ApiError(404, 'fold_job_not_found'))
  const { onStatus } = renderForm('gone')
  await flush()

  expect(onStatus).toHaveBeenCalledWith({
    message: '仕事が見つかりません（常駐サーバーを再起動すると消えます）',
    isError: true,
  })
})
