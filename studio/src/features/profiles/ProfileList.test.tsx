/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import type { ProfileItem } from '@/lib/api-types'

import { ProfileList } from './ProfileList'

afterEach(cleanup)

const ITEMS: ProfileItem[] = [
  {
    id: 'p-a',
    name: '使用中の声',
    caption: 'A の声。',
    source: 'auto',
    created_at: '2',
    tone: '',
    members: [],
    order: 'random',
    folded: false,
    speaker_model: '',
    mix: [],
  },
  {
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
    mix: [],
  },
]

function renderList(selected: string | null) {
  const onSelect = vi.fn()
  const onCreate = vi.fn()
  render(
    <ProfileList
      items={ITEMS}
      active="p-a"
      selected={selected}
      onSelect={onSelect}
      onCreate={onCreate}
    />,
  )
  return { onSelect, onCreate }
}

it('選んでいるものと使用中を示し、押すとそのプロファイルを開く', () => {
  const { onSelect } = renderList('p-b')
  const other = screen.getByRole('button', { name: /別の声/ })
  expect(other.getAttribute('aria-current')).toBe('true')
  expect(screen.getAllByText('使用中')).toHaveLength(1)

  fireEvent.click(screen.getByRole('button', { name: /使用中の声/ }))
  expect(onSelect).toHaveBeenCalledWith('p-a')
})

it('「新しく作る」で作成フォームを開き、開いている間はそれを選択中にする', () => {
  const { onCreate } = renderList(null)
  const create = screen.getByRole('button', { name: '新しく作る' })
  expect(create.getAttribute('aria-current')).toBe('true')
  fireEvent.click(create)
  expect(onCreate).toHaveBeenCalled()
})

it('ランダムの声は選び方とメンバーの名前を並べる', () => {
  render(
    <ProfileList
      items={[
        ...ITEMS,
        {
          id: 'p-r',
          name: 'ランダム',
          caption: '',
          source: 'random',
          created_at: '3',
          tone: '',
          members: ['p-a', 'p-b'],
          order: 'shuffle',
          folded: false,
          speaker_model: '',
          mix: [],
        },
      ]}
      active="p-r"
      selected="p-r"
      onSelect={vi.fn()}
      onCreate={vi.fn()}
    />,
  )
  expect(screen.getByText('シャッフル: 使用中の声・別の声')).toBeTruthy()
})

it('持ち方ごとに見出しを付けて並べ、作り方は行に添える', () => {
  render(
    <ProfileList
      items={[
        ...ITEMS,
        { ...ITEMS[1]!, id: 'p-m', name: '混ぜた', source: 'mix', speaker_model: 'm' },
      ]}
      active="p-a"
      selected={null}
      onSelect={vi.fn()}
      onCreate={vi.fn()}
    />,
  )
  const headings = screen.getAllByRole('heading').map((h) => h.textContent)
  expect(headings).toEqual(['声をそのまま保存1', '参照音声から写す2'])
  expect(screen.getByText('混ぜた声 · B の声。')).toBeTruthy()
  expect(screen.getByText('自動で作成 · A の声。')).toBeTruthy()
})

it('絞り込むと名前か caption が合うものだけ残す', () => {
  const { onSelect } = renderList('p-a')

  const search = screen.getByRole('searchbox', { name: 'プロファイルを絞り込む' })
  fireEvent.change(search, { target: { value: 'b の' } })
  expect(screen.queryByRole('button', { name: /使用中の声/ })).toBeNull()
  expect(screen.getByRole('button', { name: /別の声/ })).toBeTruthy()

  // Enter で残った先頭を開く
  fireEvent.keyDown(search, { key: 'Enter' })
  expect(onSelect).toHaveBeenCalledWith('p-b')

  fireEvent.change(search, { target: { value: '無い声' } })
  expect(screen.getByText('「無い声」に合うプロファイルはありません')).toBeTruthy()
})

it('J / K で見出しの順に隣のプロファイルへ移る', () => {
  const { onSelect } = renderList('p-b')
  // 同じ見出しの中は作り方の順（作った声 p-b → 自動で作成 p-a）
  fireEvent.keyDown(document.body, { key: 'j' })
  expect(onSelect).toHaveBeenCalledWith('p-a')
  fireEvent.keyDown(document.body, { key: 'k' })
  expect(onSelect).toHaveBeenCalledTimes(1)
})
