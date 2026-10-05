/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import type { ProfileItem } from '../../../../shared/api-types'
import { ProfileList } from './ProfileList'

afterEach(cleanup)

const ITEMS: ProfileItem[] = [
  { id: 'p-a', name: '使用中の声', caption: 'A の声。', source: 'auto', created_at: '2' },
  { id: 'p-b', name: '別の声', caption: 'B の声。', source: 'design', created_at: '1' },
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
