/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import type { ProfileItem } from '../../../../shared/api-types'
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
}

function renderCard(isActive = false) {
  const onPlay = vi.fn()
  const onStatus = vi.fn()
  const onDeleted = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ProfileCard
        item={ITEM}
        isActive={isActive}
        onPlay={onPlay}
        onStatus={onStatus}
        onDeleted={onDeleted}
      />
    </QueryClientProvider>,
  )
  return { onPlay, onStatus, onDeleted }
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
  expect(onPlay).toHaveBeenCalledWith('/profiles/p-b/audio')
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
