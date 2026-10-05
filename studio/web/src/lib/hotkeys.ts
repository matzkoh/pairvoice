import { useEffect, useEffectEvent } from 'react'

// false を返すと「何もしなかった」とみなし、preventDefault せずにブラウザへ返す
// （選択中の行が無いときの Space でページのスクロールまで奪わないように）
export type HotkeyMap = Record<string, (e: KeyboardEvent) => boolean | void>

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT'
}

const INTERACTIVE = [
  'button',
  'a[href]',
  'summary',
  '[role=button]',
  '[role=link]',
  '[role=menuitem]',
  '[role=menuitemcheckbox]',
  '[role=menuitemradio]',
  '[role=option]',
  '[role=tab]',
  '[role=checkbox]',
  '[role=radio]',
  '[role=switch]',
].join(',')

// Enter / Space はフォーカス中のボタンやメニュー項目を押す操作でもある
function activatesTarget(combo: string, target: EventTarget | null): boolean {
  if (combo !== 'enter' && combo !== 'space') return false
  return target instanceof Element && target.closest(INTERACTIVE) !== null
}

// メニュー・ダイアログ・一覧の中のキーはその部品のもの。Base UI のメニューは keydown の
// 伝播を止めないので、ここで除かないと「…」メニューを開いたまま 1 を押すと選択中の行に投票される
const OVERLAY = '[role=menu],[role=menuitem],[role=dialog],[role=alertdialog],[role=listbox]'

function isInOverlay(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(OVERLAY) !== null
}

// 押しっぱなしで繰り返してよいのは移動だけ。1 を押し続けて一覧を順に投票してしまわないように
const REPEATABLE = new Set(['j', 'k'])

// 'j' 'space' 'enter' 'mod+k' 'mod+enter' の形に正規化する。mod は ⌘（macOS）と Ctrl のどちらでもよい
function matchKey(e: KeyboardEvent): string {
  const key = e.key === ' ' ? 'space' : e.key.toLowerCase()
  return e.metaKey || e.ctrlKey ? `mod+${key}` : key
}

// 変換を確定する Enter。Safari は isComposing: false で送ってくるので keyCode 229 で見分ける
export function isImeConfirm(e: { isComposing: boolean; keyCode: number }): boolean {
  return e.isComposing || e.keyCode === 229
}

type HotkeyOptions = {
  // メニュー・ダイアログの中でも拾うキー。パレットを開閉する ⌘K のように、重なりの上で押すためのもの
  global?: readonly string[]
}

// 画面単位のショートカット。単キーは入力中に文字として打たれるものなので、
// 入力欄にフォーカスがあるときは拾わない（理想の出力を打っている最中に 1 で投票されないように）。
// メニュー・ダイアログの中では修飾キー付きも拾わない（履歴のシートを開いている間の ⌘↵ で、
// 見えていない入力欄から生成を走らせないように）
export function useHotkeys(map: HotkeyMap, { global = [] }: HotkeyOptions = {}): void {
  const onKeyDown = useEffectEvent((e: KeyboardEvent) => {
    if (e.altKey || isImeConfirm(e)) return
    const combo = matchKey(e)
    const handler = map[combo]
    if (!handler) return
    if (e.repeat && !REPEATABLE.has(combo)) return
    if (!combo.startsWith('mod+') && isTypingTarget(e.target)) return
    if (!global.includes(combo) && isInOverlay(e.target)) return
    if (activatesTarget(combo, e.target)) return
    if (handler(e) !== false) e.preventDefault()
  })

  useEffect(() => {
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])
}
