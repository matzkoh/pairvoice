/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

import { ApiError, apiSend, apiSendForm } from '@/lib/api'
import type { CurateAnalysis, CurateCandidate, CurateResponse } from '@/lib/api-types'

import { CurateProfile } from './CurateProfile'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSendForm: vi.fn(), apiSend: vi.fn() }
})

beforeEach(() => {
  localStorage.clear()
  serve(async () => RESPONSE)
})

beforeAll(() => {
  // jsdom には無い
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null)
  // タイムラインは見えている幅の分だけ区間を描く。jsdom は幅が 0 なので、画面ほどの幅にする
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => 1000,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function candidate(index: number, start: number, end: number): CurateCandidate {
  return {
    index,
    file: 0,
    start,
    end,
    snr_db: 20,
    loudness_db: -25,
    pitch_hz: 260,
    brightness_hz: 1400,
    deviation: 0.5,
  }
}

const ANALYSIS: CurateAnalysis = {
  job_id: 'job1',
  files: [{ name: 'ドラマ.mp3', offset: 0, seconds: 120 }],
  truncated: false,
  speakers: [
    { speaker: 0, seconds: 90, chosen: true },
    { speaker: 1, seconds: 30, chosen: false },
  ],
  segments: [{ start: 1, end: 60, speaker: 0 }],
  candidates: [
    candidate(0, 1, 8),
    candidate(1, 10, 16),
    candidate(2, 20, 27),
    candidate(3, 75, 80),
  ],
  preselected: [0, 1, 2],
  envelope: { step: 0.01, levels: [0, 50, 100] },
  limits: { min_seconds: 6, target_seconds: 20, max_seconds: 28 },
}

const RESPONSE: CurateResponse = {
  profile: {
    id: 'p-c',
    name: 'ドラマ',
    caption: '',
    source: 'curate',
    created_at: '1',
    tone: '',
    members: [],
    order: 'random',
  },
  report: {
    files: [{ name: 'ドラマ.mp3', seconds: 120 }],
    truncated: false,
    speakers: [{ speaker: 0, seconds: 90, chosen: true }],
    candidates: 4,
    seconds: 13,
    pieces: [{ index: 1, file: 'ドラマ.mp3', start: 75.2, end: 81.0, snr_db: 21.3 }],
  },
}

// 候補のセリフの聞き取りと、声を作る要求に答える
function serve(profile: () => Promise<CurateResponse>) {
  vi.mocked(apiSend).mockImplementation(async (path: string) => {
    const transcript = /candidates\/(\d+)\/transcript$/.exec(path)
    if (transcript) return { text: `セリフ${transcript[1]}` }
    return profile()
  })
}

function profileCalls() {
  return vi.mocked(apiSend).mock.calls.filter(([path]) => path.endsWith('/profile'))
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

async function analyze() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '解析する' }))
  })
}

function clip(start: string) {
  return screen.getByRole('button', { name: new RegExp(`^区間 ${start}〜`) })
}

it('解析した素材を送り、自動の見込みを選んだ状態で並べる', async () => {
  vi.mocked(apiSendForm).mockResolvedValue(ANALYSIS)
  renderForm()

  pick('ドラマ.mp3', '続き.mp4')
  await analyze()

  const [path, form] = vi.mocked(apiSendForm).mock.calls[0] ?? []
  expect(path).toBe('/curate')
  const sent = form?.getAll('files').map((f) => (f instanceof File ? f.name : f))
  expect(sent).toEqual(['ドラマ.mp3', '続き.mp4'])
  expect(clip('0:01').getAttribute('aria-pressed')).toBe('true')
  expect(clip('1:15').getAttribute('aria-pressed')).toBe('false')
  expect(screen.getByText(/3 区間・20.0 秒/)).toBeTruthy()
  // 名前は最初のファイルから拡張子を除いて埋める
  expect(screen.getByLabelText<HTMLInputElement>('名前').value).toBe('ドラマ')
})

it('自動で選んだ区間で作り、作った内訳を見せてから開ける', async () => {
  vi.mocked(apiSendForm).mockResolvedValue(ANALYSIS)
  serve(async () => RESPONSE)
  const { onCreated } = renderForm()
  pick('ドラマ.mp3')
  await analyze()

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'プロファイルを作る' }))
  })

  expect(profileCalls()[0]).toEqual([
    '/curate/job1/profile',
    'POST',
    { name: 'ドラマ', caption: '', pieces: [0, 1, 2] },
    { allowNotFound: true },
  ])
  expect(screen.getByText(/1 区間（候補 4 区間）から、13 秒の声/)).toBeTruthy()
  expect(screen.getByText(/ドラマ.mp3 1:15〜1:21・S\/N 21.3 dB/)).toBeTruthy()
  expect(onCreated).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '「ドラマ」を開く' }))
  expect(onCreated).toHaveBeenCalledWith('p-c')
})

it('クリックで選び直した区間の番号を送る', async () => {
  vi.mocked(apiSendForm).mockResolvedValue(ANALYSIS)
  serve(async () => RESPONSE)
  renderForm()
  pick('ドラマ.mp3')
  await analyze()

  fireEvent.click(clip('0:20'))
  fireEvent.click(clip('1:15'))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'プロファイルを作る' }))
  })

  expect(profileCalls()[0]?.[2]).toEqual({
    name: 'ドラマ',
    caption: '',
    pieces: [0, 1, 3],
  })
})

it('長さが足りなければ作れず、自動の選び方に戻せる', async () => {
  vi.mocked(apiSendForm).mockResolvedValue(ANALYSIS)
  renderForm()
  pick('ドラマ.mp3')
  await analyze()

  fireEvent.click(screen.getByRole('button', { name: 'すべて外す' }))
  expect(screen.getByText(/6 秒以上選んでください/)).toBeTruthy()
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'プロファイルを作る' }).disabled,
  ).toBe(true)

  fireEvent.click(screen.getByRole('button', { name: '自動の選び方に戻す' }))
  expect(clip('0:01').getAttribute('aria-pressed')).toBe('true')
})

it('解析が消えていたら、解析し直すよう知らせる', async () => {
  vi.mocked(apiSendForm).mockResolvedValue(ANALYSIS)
  serve(async () => {
    throw new ApiError(404, '{"error":"curate_job_not_found"}')
  })
  const { onStatus } = renderForm()
  pick('ドラマ.mp3')
  await analyze()

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'プロファイルを作る' }))
  })

  expect(onStatus).toHaveBeenCalledWith(
    expect.objectContaining({ message: expect.stringMatching(/もう一度解析/), isError: true }),
  )
  expect(screen.queryByRole('region', { name: '区間を選ぶ' })).toBeNull()
})

it('解析できなければ理由を知らせ、選んだファイルを残す', async () => {
  vi.mocked(apiSendForm).mockRejectedValue(new Error('声が見つかりません'))
  const { onStatus } = renderForm()

  pick('短い.wav')
  await analyze()

  expect(onStatus).toHaveBeenCalledWith({
    message: '解析できませんでした: 声が見つかりません',
    isError: true,
  })
  expect(screen.getByText(/短い.wav/)).toBeTruthy()
})

it('セリフ一覧に候補のセリフを順に埋め、タイムラインに切り替えられる', async () => {
  vi.mocked(apiSendForm).mockResolvedValue(ANALYSIS)
  renderForm()
  pick('ドラマ.mp3')
  await analyze()

  // 既定はセリフ一覧。候補を流れの順に1つずつ聞き取る
  expect(await screen.findByText('セリフ3')).toBeTruthy()
  const asked = vi.mocked(apiSend).mock.calls.map(([path]) => path)
  expect(asked).toEqual([0, 1, 2, 3].map((i) => `/curate/job1/candidates/${i}/transcript`))
  expect(clip('0:01').title).toContain('「セリフ0」')

  fireEvent.click(screen.getByRole('button', { name: 'タイムライン' }))
  expect(screen.getByRole('button', { name: 'タイムライン' }).getAttribute('aria-pressed')).toBe(
    'true',
  )
  expect(screen.getByRole('button', { name: '縮小' })).toBeTruthy()
  // 選んだ区間は並べ方を変えても残る
  expect(clip('0:01').getAttribute('aria-pressed')).toBe('true')
  expect(localStorage.getItem('pairvoice.curate.layout')).toBe('timeline')
})
