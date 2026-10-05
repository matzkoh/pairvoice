import { expect, it } from 'vitest'

import { type Command, filterCommands } from './commands'

const run = () => {}
const COMMANDS: Command[] = [
  { id: 'nav:/dict', label: '辞書', group: '移動', run },
  { id: 'nav:/profiles', label: 'プロファイル', group: '移動', run },
  { id: 'mute:30', label: '30分ミュート', group: 'ミュート', run },
]

it('空なら全部を元の順で返す', () => {
  expect(filterCommands(COMMANDS, '  ').map((c) => c.id)).toEqual([
    'nav:/dict',
    'nav:/profiles',
    'mute:30',
  ])
})

it('ラベルとグループ名の部分一致で絞る（大文字小文字を区別しない）', () => {
  expect(filterCommands(COMMANDS, '辞書').map((c) => c.id)).toEqual(['nav:/dict'])
  expect(filterCommands(COMMANDS, 'ミュート').map((c) => c.id)).toEqual(['mute:30'])
})
