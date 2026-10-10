/** @vitest-environment jsdom */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import { initialValues } from './samplerKnobs'
import { type TakeInput, seedsFor, useTakes } from './useTakes'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function input(caption: string): TakeInput {
  return { text: 'テスト', caption, values: initialValues(), sampler: { num_steps: 40 } }
}

function Harness() {
  const { takes, running, start, stop, retry, clear } = useTakes()
  const oldest = takes.at(-1)
  return (
    <div>
      <button type="button" onClick={() => start({ input: input('声A'), count: 3, seedBase: 10 })}>
        A を3件
      </button>
      <button type="button" onClick={() => start({ input: input('声B'), count: 1, seedBase: 20 })}>
        B を1件
      </button>
      <button type="button" onClick={stop}>
        中止
      </button>
      <button type="button" onClick={() => oldest && retry(oldest.id)}>
        最古を再生成
      </button>
      <button type="button" onClick={clear}>
        クリア
      </button>
      <p data-testid="running">{running ? 'running' : 'idle'}</p>
      <ul>
        {takes.map((t) => (
          <li key={t.id} data-testid="take">
            {t.seed}:{t.status}:{t.input.caption}:{t.message ?? '-'}
          </li>
        ))}
      </ul>
    </div>
  )
}

function rows() {
  return screen.queryAllByTestId('take').map((el) => el.textContent)
}

async function click(name: string) {
  await act(async () => {
    screen.getByRole('button', { name }).click()
  })
}

function sentSeeds() {
  return vi.mocked(apiSend).mock.calls.map((call) => call[2])
}

const OK = { relative_path: 'generations/x.wav', duration: 1.5 }

it('seed を埋めれば連番、空欄なら候補ごとに違う乱数', () => {
  expect(seedsFor(10, 3)).toEqual([10, 11, 12])
  expect(new Set(seedsFor(null, 3, () => 0.5)).size).toBe(3)
})

it('seed 連番で1件ずつ順に合成する', async () => {
  vi.mocked(apiSend).mockResolvedValue(OK)
  render(<Harness />)
  await click('A を3件')
  expect(sentSeeds()).toMatchObject([
    { sampler: { rng_seed: 10 } },
    { sampler: { rng_seed: 11 } },
    { sampler: { rng_seed: 12 } },
  ])
  expect(rows()).toEqual(['10:done:声A:-', '11:done:声A:-', '12:done:声A:-'])
})

it('次の生成は前のテイクを残して上に積む', async () => {
  vi.mocked(apiSend).mockResolvedValue(OK)
  render(<Harness />)
  await click('A を3件')
  await click('B を1件')
  expect(rows()[0]).toBe('20:done:声B:-')
  expect(rows()).toHaveLength(4)
})

it('失敗したらそのテイクに理由を出し、残りの待機テイクは捨てる', async () => {
  vi.mocked(apiSend).mockResolvedValueOnce(OK).mockResolvedValueOnce({ error: 'model_load_failed' })
  render(<Harness />)
  await click('A を3件')
  expect(rows()).toEqual(['10:done:声A:-', '11:error:声A:model_load_failed'])
  expect(screen.getByTestId('running').textContent).toBe('idle')
})

it('再生成はそのテイクを作ったときの条件で行う', async () => {
  vi.mocked(apiSend).mockResolvedValue(OK)
  render(<Harness />)
  await click('A を3件')
  await click('B を1件')
  vi.mocked(apiSend).mockClear()
  await click('最古を再生成')
  expect(apiSend).toHaveBeenCalledWith(
    '/synthesize',
    'POST',
    expect.objectContaining({ caption: '声A', sampler: { num_steps: 40, rng_seed: 12 } }),
  )
})

it('生成中の start は無視する', async () => {
  const resolvers: ((value: unknown) => void)[] = []
  vi.mocked(apiSend).mockImplementation(() => new Promise((r) => resolvers.push(r)))
  render(<Harness />)
  await click('B を1件')
  await click('A を3件')
  expect(apiSend).toHaveBeenCalledTimes(1)
  await act(async () => resolvers[0]?.(OK))
})

it('同一 tick で start を2回呼んでも二重にループが走らない', () => {
  vi.mocked(apiSend).mockImplementation(() => new Promise(() => {}))
  render(<Harness />)
  act(() => {
    screen.getByRole('button', { name: 'A を3件' }).click()
    screen.getByRole('button', { name: 'A を3件' }).click()
  })
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(rows()).toHaveLength(3)
})

it('中止すると以降を投げないが、進行中の1件は捨てない', async () => {
  const resolvers: ((value: unknown) => void)[] = []
  vi.mocked(apiSend).mockImplementation(
    () => new Promise<unknown>((resolve) => resolvers.push(resolve)),
  )
  render(<Harness />)
  act(() => {
    screen.getByRole('button', { name: 'A を3件' }).click()
  })
  act(() => {
    screen.getByRole('button', { name: '中止' }).click()
  })
  await act(async () => {
    resolvers[0]?.(OK)
  })
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(rows()).toEqual(['10:done:声A:-'])
  expect(screen.getByTestId('running').textContent).toBe('idle')
})

it('生成中の retry は無視する', async () => {
  vi.mocked(apiSend).mockResolvedValue(OK)
  render(<Harness />)
  await click('A を3件')
  expect(apiSend).toHaveBeenCalledTimes(3)

  vi.mocked(apiSend).mockImplementation(() => new Promise(() => {}))
  act(() => {
    screen.getByRole('button', { name: '最古を再生成' }).click()
    screen.getByRole('button', { name: '最古を再生成' }).click()
  })
  expect(apiSend).toHaveBeenCalledTimes(4)
})

it('再生成は同じ枠を差し替え、テイクは増えない', async () => {
  vi.mocked(apiSend).mockResolvedValue(OK)
  render(<Harness />)
  await click('A を3件')
  vi.mocked(apiSend).mockResolvedValue({ relative_path: 'generations/y.wav', duration: 2 })
  await click('最古を再生成')
  expect(rows()).toEqual(['10:done:声A:-', '11:done:声A:-', '12:done:声A:-'])
})

it('クリアで空になる', async () => {
  vi.mocked(apiSend).mockResolvedValue(OK)
  render(<Harness />)
  await click('B を1件')
  await click('クリア')
  expect(rows()).toEqual([])
})

it('アンマウントしたら、残りのテイクを投げず、鳴らす通知も出さない', async () => {
  const resolvers: ((value: unknown) => void)[] = []
  vi.mocked(apiSend).mockImplementation(() => new Promise((r) => resolvers.push(r)))
  const onReady = vi.fn()
  function Unmountable() {
    const { start } = useTakes({ onReady })
    return (
      <button type="button" onClick={() => start({ input: input('声A'), count: 3, seedBase: 10 })}>
        A を3件
      </button>
    )
  }
  const { unmount } = render(<Unmountable />)
  await click('A を3件')
  unmount()
  await act(async () => resolvers[0]?.(OK))
  expect(apiSend).toHaveBeenCalledTimes(1)
  expect(onReady).not.toHaveBeenCalled()
})
