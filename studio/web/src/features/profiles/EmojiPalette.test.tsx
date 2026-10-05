/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useRef, useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { EMOJI_ANNOTATIONS } from './emojiAnnotations'
import { EmojiPalette } from './EmojiPalette'
import { useEmojiInsert } from './useEmojiInsert'

afterEach(cleanup)

// EMOJI_ANNOTATIONS.length との突き合わせだけでは、データ自体が1件消えても
// 両者が揃って減るため気づけない。原典（HuggingFace の EMOJI_ANNOTATIONS.md）と
// 一致する件数を固定で見る。
it('原典と同じ45件を持つ', () => {
  expect(EMOJI_ANNOTATIONS).toHaveLength(45)
})

it('注釈の全件をボタンとして並べる', () => {
  render(<EmojiPalette onInsert={vi.fn()} />)

  expect(screen.getAllByRole('button')).toHaveLength(EMOJI_ANNOTATIONS.length)
})

it('絵文字だけでは意味が読めないので日本語ラベルも出す', () => {
  render(<EmojiPalette onInsert={vi.fn()} />)

  expect(screen.getByText('囁き、耳元の音')).toBeTruthy()
})

it('クリックでその絵文字を渡す', () => {
  const onInsert = vi.fn()
  render(<EmojiPalette onInsert={onInsert} />)

  screen.getByRole('button', { name: /囁き/ }).click()

  expect(onInsert).toHaveBeenCalledWith('👂')
})

// 実ブラウザは mousedown の時点でフォーカスをクリック対象へ移す（jsdom の
// fireEvent.click はこれをしないため、この回帰は通常の click ベースのテストでは
// 検出できない）。preventDefault していないと、ボタンを押すたびテキスト欄のフォーカスが
// 奪われ、useEmojiInsert がカーソル位置を見失って常に末尾へ追記してしまう。
it('ボタンの mousedown は既定動作を打ち消し、テキスト欄のフォーカスを奪わない', () => {
  render(<EmojiPalette onInsert={vi.fn()} />)
  const button = screen.getByRole('button', { name: /囁き/ })

  const notPrevented = fireEvent.mouseDown(button)

  expect(notPrevented).toBe(false)
})

function IntegrationHarness({ initial }: { initial: string }) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [value, setValue] = useState(initial)
  const insert = useEmojiInsert(ref, value, setValue)
  return (
    <div>
      <textarea ref={ref} value={value} onChange={(e) => setValue(e.target.value)} />
      <EmojiPalette onInsert={insert} />
    </div>
  )
}

// mousedown の preventDefault を実ブラウザ相当に効かせる。jsdom は「preventDefault
// されなければ mousedown でフォーカスが移る」を自動ではやってくれないので、その既定動作を
// ここで模して、EmojiPalette と useEmojiInsert を組み合わせたときに実機の欠陥
// （ボタンにフォーカスが奪われてカーソル位置を見失う）が再発しないことを確かめる。
it('実ブラウザのフォーカス遷移を模しても、テキスト欄のカーソル位置に挿入される', () => {
  render(<IntegrationHarness initial="あいうえお" />)
  const el = screen.getByRole<HTMLTextAreaElement>('textbox')
  const button = screen.getByRole('button', { name: /囁き/ })
  el.focus()
  el.setSelectionRange(2, 2)

  const notPrevented = fireEvent.mouseDown(button)
  if (notPrevented) button.focus() // 実ブラウザの既定動作（フォーカス移動）を模す
  fireEvent.click(button)

  expect(el.value).toBe('あい👂うえお')
})
