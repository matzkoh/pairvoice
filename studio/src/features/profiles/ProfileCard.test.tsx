/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'

import { ProfileCard } from './ProfileCard'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const ITEM: ProfileItem = {
  id: 'p-b',
  name: '別の声',
  caption: 'B の声。',
  source: 'design',
  created_at: '1',
  tone: '',
  members: [],
  order: 'random',
  folded: false,
  speaker_model: '',
  reads_from_reference: false,
  mix: [],
  muffled: null,
}

const VOICES: ProfileItem[] = [
  { ...ITEM, id: 'p-a', name: '一つ目' },
  ITEM,
  { ...ITEM, id: 'p-c', name: '三つ目' },
]

const RANDOM: ProfileItem = {
  ...ITEM,
  id: 'p-r',
  name: 'ランダム',
  caption: '',
  source: 'random',
  members: ['p-a', 'p-b'],
  order: 'random',
  folded: false,
  speaker_model: '',
  reads_from_reference: false,
  mix: [],
  muffled: null,
}

function renderCard(isActive = false, item: ProfileItem = ITEM, unmuffleAvailable = true) {
  const onPlay = vi.fn()
  const onStatus = vi.fn()
  const onDeleted = vi.fn()
  const onCreated = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ProfileCard
        item={item}
        voices={VOICES}
        isActive={isActive}
        unmuffleAvailable={unmuffleAvailable}
        onPlay={onPlay}
        onStatus={onStatus}
        onDeleted={onDeleted}
        onCreated={onCreated}
      />
    </QueryClientProvider>,
  )
  return { onPlay, onStatus, onDeleted, onCreated }
}

it('参照音声と caption を中身として見せる', () => {
  renderCard()
  expect(screen.getByText('参照音声')).toBeTruthy()
  expect(screen.getByText('B の声。')).toBeTruthy()
})

it('使用中には印を出し、「使う」と「削除」を出さない', () => {
  renderCard(true)
  expect(screen.getByText('使用中')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '使う' })).toBeNull()
  expect(screen.queryByRole('button', { name: '削除' })).toBeNull()
})

it('「使う」で使用中を切り替える', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const { onStatus } = renderCard()

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '使う' }))
  })

  expect(apiSend).toHaveBeenCalledWith('/profiles/active', 'PUT', { id: 'p-b' })
  expect(onStatus).toHaveBeenCalledWith(expect.objectContaining({ isError: false }))
})

it('削除したら知らせる', async () => {
  vi.mocked(apiSend).mockResolvedValue({ ok: true })
  const { onDeleted } = renderCard()

  fireEvent.click(screen.getByRole('button', { name: '削除' }))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '本当に削除' }))
  })

  expect(apiSend).toHaveBeenCalledWith('/profiles/p-b', 'DELETE')
  expect(onDeleted).toHaveBeenCalled()
})

it('参照音声を再生できる', () => {
  const { onPlay } = renderCard()
  fireEvent.click(screen.getByRole('button', { name: '別の声 の参照音声を再生' }))
  expect(onPlay).toHaveBeenCalledWith('/api/profiles/p-b/audio')
})

it('表現を保存した声には、そのまま保存していることと、参照音声を読み上げに使わないと書く', () => {
  renderCard(false, { ...ITEM, folded: true, speaker_model: 'tts/now' })

  expect(screen.getByText('声をそのまま保存')).toBeTruthy()
  expect(screen.getByText(/ふだんの読み上げには使いません/)).toBeTruthy()
})

it('参照音声から写す声には、モデルを替えても読めることと、わずかに違うことを書く', () => {
  renderCard()

  expect(screen.getByText('参照音声から写す')).toBeTruthy()
  expect(screen.getByText(/わずかに違う/)).toBeTruthy()
  expect(screen.queryByText(/ふだんの読み上げには使いません/)).toBeNull()
})

it('別の合成モデルで保存した声は、参照音声から写して読んでいると知らせる', () => {
  renderCard(false, {
    ...ITEM,
    source: 'mix',
    speaker_model: 'tts/old',
    reads_from_reference: true,
  })

  expect(screen.getByText('参照音声から写して読んでいます')).toBeTruthy()
  expect(screen.getByText(/tts\/old/)).toBeTruthy()
})

it('名前をクリックすると変更でき、Enter で保存する', async () => {
  vi.mocked(apiSend).mockResolvedValue({})
  renderCard()

  fireEvent.click(screen.getByRole('button', { name: '別の声' }))
  const input = screen.getByRole('textbox', { name: 'プロファイルの名前' })
  fireEvent.change(input, { target: { value: '新しい名前' } })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' })
  })

  expect(apiSend).toHaveBeenCalledWith('/profiles/p-b', 'PATCH', { name: '新しい名前' })
})

// 入力欄が消えるとき、ブラウザはフォーカスを失った blur を送ってくることがある
it('Escape の後に blur が来ても保存しない。Enter の後に blur が来ても二重に保存しない', async () => {
  vi.mocked(apiSend).mockResolvedValue({})
  renderCard()

  fireEvent.click(screen.getByRole('button', { name: '別の声' }))
  let input = screen.getByRole('textbox', { name: 'プロファイルの名前' })
  fireEvent.change(input, { target: { value: '捨てる名前' } })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Escape' })
    fireEvent.blur(input)
  })
  expect(apiSend).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: '別の声' }))
  input = screen.getByRole('textbox', { name: 'プロファイルの名前' })
  fireEvent.change(input, { target: { value: '新しい名前' } })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.blur(input)
  })
  expect(apiSend).toHaveBeenCalledTimes(1)
})

it('ランダムの声は参照音声と caption の代わりにメンバーを見せ、変えたら保存できる', async () => {
  vi.mocked(apiSend).mockResolvedValue(RANDOM)
  renderCard(false, RANDOM)

  expect(screen.queryByText('参照音声')).toBeNull()
  const save = screen.getByRole('button', { name: '保存' })
  expect(save.hasAttribute('disabled')).toBe(true)
  // 1つに減らすと選ぶ意味が無いので保存させない
  fireEvent.click(screen.getByRole('checkbox', { name: '別の声' }))
  expect(save.hasAttribute('disabled')).toBe(true)

  fireEvent.click(screen.getByRole('checkbox', { name: '三つ目' }))
  await act(async () => {
    fireEvent.click(save)
  })

  expect(apiSend).toHaveBeenCalledWith('/profiles/p-r', 'PATCH', {
    members: ['p-a', 'p-c'],
    order: 'random',
  })
})

it('ランダムの声は選び方だけを変えても保存できる', async () => {
  vi.mocked(apiSend).mockResolvedValue(RANDOM)
  renderCard(false, RANDOM)

  fireEvent.click(screen.getByRole('radio', { name: /シャッフル/ }))
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
  })

  expect(apiSend).toHaveBeenCalledWith('/profiles/p-r', 'PATCH', {
    members: ['p-a', 'p-b'],
    order: 'shuffle',
  })
})

it('混ぜた声は、もとの声と割合を見せる（消した声も残す）', () => {
  renderCard(false, {
    ...ITEM,
    id: 'p-m',
    source: 'mix',
    mix: [
      { id: 'p-a', weight: 0.25 },
      { id: 'p-gone', weight: 0.75 },
    ],
  })
  expect(screen.getByText('一つ目 25% / （削除した声） 75%')).toBeTruthy()
})

const MUFFLED: ProfileItem = { ...ITEM, source: 'mix', muffled: true }

it('こもっていない声には、こもりの知らせも直す操作も出さない', () => {
  renderCard(false, { ...ITEM, muffled: false })

  expect(screen.queryByText('こもった音です')).toBeNull()
  expect(screen.queryByRole('button', { name: 'こもりを取る' })).toBeNull()
})

it('こもった声は量を探して前後を聴かせ、元の声を残したまま別の声として保存する', async () => {
  vi.mocked(apiSend).mockImplementation(async (path: string) => {
    if (path.endsWith('/unmuffle')) {
      return {
        status: 'fixed',
        amount: 2.5,
        before: { audio: 'generations/before.wav', worst_db: -60 },
        after: { audio: 'generations/after.wav', worst_db: -20 },
      }
    }
    return { ...MUFFLED, id: 'p-new', name: '直した声', muffled: false }
  })
  const { onPlay, onStatus, onCreated } = renderCard(false, MUFFLED)

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'こもりを取る' }))
  })
  fireEvent.click(screen.getByRole('button', { name: '直した後を再生' }))
  expect(String(onPlay.mock.calls[0]![0])).toContain('after.wav')
  fireEvent.change(screen.getByLabelText('直した声の名前'), { target: { value: '直した声' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '別の声として保存' }))
  })

  expect(apiSend).toHaveBeenCalledWith(`/profiles/${MUFFLED.id}/unmuffle`, 'POST')
  // 元の声1つを重み1で、探した量だけ直す。元の声は書き換えない
  expect(apiSend).toHaveBeenCalledWith('/profiles/mix', 'POST', {
    name: '直した声',
    caption: MUFFLED.caption,
    voices: [{ voice: MUFFLED.id, weight: 1 }],
    unmuffle: 2.5,
  })
  expect(vi.mocked(apiSend).mock.calls.some(([, method]) => method === 'PATCH')).toBe(false)
  expect(onCreated).toHaveBeenCalledWith('p-new')
  expect(onStatus).toHaveBeenLastCalledWith({
    message: '「直した声」を保存しました',
    isError: false,
  })
})

it('直せなかったときと、直す必要がなかったときは、そう伝えて保存させない', async () => {
  vi.mocked(apiSend).mockResolvedValueOnce({
    status: 'unfixable',
    amount: null,
    before: { audio: 'generations/before.wav', worst_db: -70 },
    after: null,
  })
  renderCard(false, MUFFLED)

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'こもりを取る' }))
  })

  expect(screen.getByText(/上限でも、こもりが取れませんでした/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: '別の声として保存' })).toBeNull()
})

it('いまの合成モデルで直せないときは、知らせだけ出してボタンを出さない', () => {
  renderCard(false, MUFFLED, false)

  expect(screen.getByText(/いまの合成モデルでは直せません/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'こもりを取る' })).toBeNull()
})
