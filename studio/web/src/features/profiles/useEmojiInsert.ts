import { type RefObject, useEffect, useRef } from 'react'

// 絵文字はテキストのどこに置くかで効き方が変わるので、末尾に足すのではなくカーソル位置に
// 挿入する。挿入後にフォーカスとキャレットを戻すのは、連続クリックで同じ絵文字を重ねる
// 作法（原典の "using the same emoji multiple times"）を使えるようにするため。
//
// キャレットの復元を useEffect に置くのは、onChange を呼んだ時点では textarea の value が
// まだ更新されておらず、setSelectionRange が古い長さに対して効いてしまうため。
// 依存配列は持たない。挿入が pendingCaret を予約した直後の描画でだけ働き、それ以外は素通りする。
export function useEmojiInsert(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
  onChange: (next: string) => void,
): (emoji: string) => void {
  const pendingCaret = useRef<number | null>(null)

  useEffect(() => {
    const caret = pendingCaret.current
    if (caret === null) return
    pendingCaret.current = null
    const el = ref.current
    if (!el) return
    el.focus()
    el.setSelectionRange(caret, caret)
  })

  return (emoji: string) => {
    const el = ref.current
    const focused = el !== null && document.activeElement === el
    // 一度もフォーカスしていなければ selectionStart は 0 で、先頭に入ってしまう。
    // 押した人の意図は「いま書いている続き」なので末尾に足す
    const start = focused ? el.selectionStart : value.length
    const end = focused ? el.selectionEnd : value.length
    const next = `${value.slice(0, start)}${emoji}${value.slice(end)}`
    pendingCaret.current = start + emoji.length
    onChange(next)
  }
}
