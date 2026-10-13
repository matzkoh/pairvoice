/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'

import { memberCandidates } from './MemberPicker'
import { RandomProfile } from './RandomProfile'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const VOICE: ProfileItem = {
  id: 'p-a',
  name: '一つ目',
  caption: '',
  source: 'design',
  created_at: '1',
  tone: '',
  members: [],
  order: 'random',
}
const VOICES = [VOICE, { ...VOICE, id: 'p-b', name: '二つ目' }]

function renderForm(voices: ProfileItem[] = VOICES) {
  const onCreated = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RandomProfile voices={voices} onStatus={vi.fn()} onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return { onCreated }
}

it('ランダムの声はメンバーの候補にしない', () => {
  const random: ProfileItem = { ...VOICE, id: 'p-r', source: 'random', members: ['p-a', 'p-b'] }
  expect(memberCandidates([...VOICES, random]).map((v) => v.id)).toEqual(['p-a', 'p-b'])
})

it('名前とメンバー2つを選ぶと作れる', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ...VOICE, id: 'p-r', name: 'ランダム' })
  const { onCreated } = renderForm()
  const create = screen.getByRole('button', { name: 'プロファイルを作る' })

  fireEvent.change(screen.getByLabelText('名前'), { target: { value: ' ランダム ' } })
  fireEvent.click(screen.getByRole('checkbox', { name: '一つ目' }))
  expect(create.hasAttribute('disabled')).toBe(true)
  fireEvent.click(screen.getByRole('checkbox', { name: '二つ目' }))
  await act(async () => {
    fireEvent.click(create)
  })

  expect(apiSend).toHaveBeenCalledWith('/profiles/random', 'POST', {
    name: 'ランダム',
    members: ['p-a', 'p-b'],
    order: 'random',
  })
  expect(onCreated).toHaveBeenCalledWith('p-r')
})

it('シャッフルを選ぶと shuffle で作る', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ...VOICE, id: 'p-s', name: '順番' })
  renderForm()

  fireEvent.change(screen.getByLabelText('名前'), { target: { value: '順番' } })
  fireEvent.click(screen.getByRole('checkbox', { name: '一つ目' }))
  fireEvent.click(screen.getByRole('checkbox', { name: '二つ目' }))
  fireEvent.click(screen.getByRole('radio', { name: /シャッフル/ }))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'プロファイルを作る' }))
  })

  expect(apiSend).toHaveBeenCalledWith('/profiles/random', 'POST', {
    name: '順番',
    members: ['p-a', 'p-b'],
    order: 'shuffle',
  })
})

it('声が2つに満たなければ先に作るよう促す', () => {
  renderForm([VOICE])
  expect(screen.getByText(/先に声を2つ以上作ります/)).toBeTruthy()
})
