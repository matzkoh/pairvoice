import { expect, it } from 'vitest'

import type { ProfileItem } from '@/lib/api-types'

import { createdStatus } from './queries'

const created = { name: '新しい声', muffled: false } as ProfileItem

it('こもった声を作ったら、保存は済ませたうえで直し方を添える', () => {
  expect(createdStatus(created, '保存しました').message).toBe('「新しい声」を保存しました')
  expect(createdStatus({ ...created, muffled: true }, '保存しました')).toEqual({
    message: '「新しい声」を保存しました。こもった音です。声の画面の「こもりを取る」で直せます',
    isError: false,
  })
})
