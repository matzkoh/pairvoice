/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useRef, useState } from 'react'
import { afterEach, expect, it } from 'vitest'

import { useEmojiInsert } from './useEmojiInsert'

afterEach(cleanup)

function Harness({ initial }: { initial: string }) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [value, setValue] = useState(initial)
  const insert = useEmojiInsert(ref, value, setValue)
  return (
    <div>
      <textarea ref={ref} value={value} onChange={(e) => setValue(e.target.value)} />
      <button type="button" onClick={() => insert('👂')}>
        挿入
      </button>
    </div>
  )
}

function textarea() {
  return screen.getByRole<HTMLTextAreaElement>('textbox')
}

it('カーソル位置に挿入する', () => {
  render(<Harness initial="あいうえお" />)
  const el = textarea()
  el.focus()
  el.setSelectionRange(2, 2)

  fireEvent.click(screen.getByRole('button', { name: '挿入' }))

  expect(el.value).toBe('あい👂うえお')
})

it('選択範囲があればそれを置き換える', () => {
  render(<Harness initial="あいうえお" />)
  const el = textarea()
  el.focus()
  el.setSelectionRange(1, 3)

  fireEvent.click(screen.getByRole('button', { name: '挿入' }))

  expect(el.value).toBe('あ👂えお')
})

it('連続で押すと同じ絵文字が重なる（効果を強める作法が使える）', () => {
  render(<Harness initial="あ" />)
  const el = textarea()
  el.focus()
  el.setSelectionRange(1, 1)

  fireEvent.click(screen.getByRole('button', { name: '挿入' }))
  fireEvent.click(screen.getByRole('button', { name: '挿入' }))
  fireEvent.click(screen.getByRole('button', { name: '挿入' }))

  expect(el.value).toBe('あ👂👂👂')
})

it('挿入後もフォーカスが戻る', () => {
  render(<Harness initial="あ" />)
  const el = textarea()
  el.focus()
  el.setSelectionRange(1, 1)

  fireEvent.click(screen.getByRole('button', { name: '挿入' }))

  expect(document.activeElement).toBe(el)
})

it('一度もフォーカスしていなければ末尾に足す', () => {
  render(<Harness initial="あい" />)

  fireEvent.click(screen.getByRole('button', { name: '挿入' }))

  expect(textarea().value).toBe('あい👂')
})
