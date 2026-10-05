/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { type HotkeyMap, useHotkeys } from './hotkeys'

afterEach(cleanup)

function Harness({ map, global }: { map: HotkeyMap; global?: string[] }) {
  useHotkeys(map, { global })
  return (
    <div>
      <input aria-label="入力欄" />
      <button type="button">ボタン</button>
      <div role="menuitem" tabIndex={-1}>
        項目
      </div>
      <div role="dialog" aria-label="ダイアログ">
        <button type="button">ダイアログ内のボタン</button>
      </div>
    </div>
  )
}

it('単キーと Space を拾う', () => {
  const j = vi.fn()
  const space = vi.fn()
  render(<Harness map={{ j, space }} />)
  fireEvent.keyDown(document.body, { key: 'j' })
  fireEvent.keyDown(document.body, { key: ' ' })
  expect(j).toHaveBeenCalledTimes(1)
  expect(space).toHaveBeenCalledTimes(1)
})

it('入力欄にいるときは単キーを拾わない', () => {
  const one = vi.fn()
  render(<Harness map={{ '1': one }} />)
  fireEvent.keyDown(screen.getByLabelText('入力欄'), { key: '1' })
  expect(one).not.toHaveBeenCalled()
})

it('修飾キー付きは入力欄にいても拾う（⌘ でも Ctrl でも）', () => {
  const open = vi.fn()
  render(<Harness map={{ 'mod+k': open }} />)
  fireEvent.keyDown(screen.getByLabelText('入力欄'), { key: 'k', metaKey: true })
  fireEvent.keyDown(document.body, { key: 'k', ctrlKey: true })
  expect(open).toHaveBeenCalledTimes(2)
})

it('Alt 付きや登録していないキーは素通しにする', () => {
  const j = vi.fn()
  render(<Harness map={{ j }} />)
  const notPrevented = fireEvent.keyDown(document.body, { key: 'j', altKey: true })
  fireEvent.keyDown(document.body, { key: 'x' })
  expect(j).not.toHaveBeenCalled()
  expect(notPrevented).toBe(true)
})

it('ボタンやメニュー項目の上の Enter / Space はブラウザに任せる', () => {
  const enter = vi.fn()
  const space = vi.fn()
  render(<Harness map={{ enter, space }} />)
  const button = screen.getByRole('button', { name: 'ボタン' })
  expect(fireEvent.keyDown(button, { key: 'Enter' })).toBe(true)
  expect(fireEvent.keyDown(button, { key: ' ' })).toBe(true)
  expect(fireEvent.keyDown(screen.getByRole('menuitem'), { key: 'Enter' })).toBe(true)
  expect(enter).not.toHaveBeenCalled()
  expect(space).not.toHaveBeenCalled()
})

it('ボタンの上でも Enter / Space 以外の単キーは拾う', () => {
  const j = vi.fn()
  render(<Harness map={{ j }} />)
  fireEvent.keyDown(screen.getByRole('button', { name: 'ボタン' }), { key: 'j' })
  expect(j).toHaveBeenCalledTimes(1)
})

it('ハンドラが false を返したら preventDefault しない', () => {
  const space = vi.fn(() => false)
  const j = vi.fn()
  render(<Harness map={{ space, j }} />)
  expect(fireEvent.keyDown(document.body, { key: ' ' })).toBe(true)
  expect(space).toHaveBeenCalledTimes(1)
  expect(fireEvent.keyDown(document.body, { key: 'j' })).toBe(false)
})

it('メニュー・ダイアログの中では修飾キー付きも含めて拾わない', () => {
  const one = vi.fn()
  const j = vi.fn()
  const generate = vi.fn()
  render(<Harness map={{ '1': one, j, 'mod+enter': generate }} />)
  fireEvent.keyDown(screen.getByRole('menuitem'), { key: '1' })
  const inDialog = screen.getByRole('button', { name: 'ダイアログ内のボタン' })
  fireEvent.keyDown(inDialog, { key: 'j' })
  const notPrevented = fireEvent.keyDown(inDialog, { key: 'Enter', metaKey: true })
  expect(one).not.toHaveBeenCalled()
  expect(j).not.toHaveBeenCalled()
  expect(generate).not.toHaveBeenCalled()
  expect(notPrevented).toBe(true)
})

it('global に挙げたキーはダイアログの中でも拾う', () => {
  const open = vi.fn()
  const generate = vi.fn()
  render(<Harness map={{ 'mod+k': open, 'mod+enter': generate }} global={['mod+k']} />)
  const inDialog = screen.getByRole('button', { name: 'ダイアログ内のボタン' })
  fireEvent.keyDown(inDialog, { key: 'k', metaKey: true })
  fireEvent.keyDown(inDialog, { key: 'Enter', metaKey: true })
  expect(open).toHaveBeenCalledTimes(1)
  expect(generate).not.toHaveBeenCalled()
})

it('IME の変換を確定するキーは拾わない（Safari の keyCode 229 も）', () => {
  const enter = vi.fn()
  render(<Harness map={{ enter }} />)
  fireEvent.keyDown(document.body, { key: 'Enter', isComposing: true })
  fireEvent.keyDown(document.body, { key: 'Enter', keyCode: 229 })
  expect(enter).not.toHaveBeenCalled()
})

it('押しっぱなしの繰り返しは j / k だけ拾う', () => {
  const one = vi.fn()
  const j = vi.fn()
  render(<Harness map={{ '1': one, j }} />)
  fireEvent.keyDown(document.body, { key: '1' })
  fireEvent.keyDown(document.body, { key: '1', repeat: true })
  fireEvent.keyDown(document.body, { key: 'j' })
  fireEvent.keyDown(document.body, { key: 'j', repeat: true })
  expect(one).toHaveBeenCalledTimes(1)
  expect(j).toHaveBeenCalledTimes(2)
})
