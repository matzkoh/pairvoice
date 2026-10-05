import { useNavigate } from '@tanstack/react-router'
import { cn } from 'cn'
import { useRef, useState } from 'react'

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { MUTE_CHOICES, useMute } from '@/features/health/useMute'
import { isImeConfirm, useHotkeys } from '@/lib/hotkeys'

import { type Command, filterCommands } from './commands'
import { NAV_GROUPS } from './nav'

export function CommandMenu() {
  const [open, setOpen] = useState(false)
  const navigatedRef = useRef(false)
  const mute = useMute()

  function toggle() {
    navigatedRef.current = false
    setOpen((v) => !v)
  }

  function run(command: Command) {
    navigatedRef.current = command.navigates === true
    setOpen(false)
    command.run()
  }

  useHotkeys({ 'mod+k': toggle }, { global: ['mod+k'] })

  return (
    <>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          showCloseButton={false}
          // 画面を移った後に、開く前の要素（辞書に追加のボタンなど）へフォーカスを戻すと、
          // 続けて打った Enter がそのボタンを押してしまう
          finalFocus={() => !navigatedRef.current}
          className="top-[20%] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-lg"
        >
          <DialogTitle className="sr-only">コマンド</DialogTitle>
          <CommandSearch onChooseMute={mute.choose} onRun={run} />
        </DialogContent>
      </Dialog>
      {/* 実行するとパレットは閉じる。失敗はその後に出るので、パレットの外に置かないと見えない */}
      {mute.error ? (
        <p
          role="alert"
          className="fixed right-4 bottom-4 z-50 max-w-sm rounded-md border bg-card px-3 py-2 text-xs text-destructive shadow-md"
        >
          ミュートを変更できませんでした: {mute.error}
        </p>
      ) : null}
    </>
  )
}

// 閉じるとポップアップごと外れるので、検索語と選択位置は開くたびに初期状態から始まる
function CommandSearch({
  onChooseMute,
  onRun,
}: {
  onChooseMute: (minutes: number | null) => void
  onRun: (command: Command) => void
}) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const navigate = useNavigate()

  const commands: Command[] = [
    ...NAV_GROUPS.flatMap((group) =>
      group.items.map((item) => ({
        id: `nav:${item.to}`,
        label: item.label,
        group: '移動',
        navigates: true,
        run: () => void navigate({ to: item.to }),
      })),
    ),
    ...MUTE_CHOICES.map((choice) => ({
      id: `mute:${choice.minutes ?? 'off'}`,
      label: choice.label,
      group: 'ミュート',
      run: () => onChooseMute(choice.minutes),
    })),
  ]
  const results = filterCommands(commands, query)

  function run(command: Command | undefined) {
    if (command) onRun(command)
  }

  return (
    <>
      <input
        role="combobox"
        aria-label="コマンドを検索"
        aria-expanded="true"
        aria-controls="command-results"
        aria-activedescendant={results[active] ? `command-${results[active].id}` : undefined}
        autoFocus
        value={query}
        placeholder="移動先や操作を検索"
        className="w-full border-b bg-transparent px-4 py-3 text-sm outline-none"
        onChange={(e) => {
          setQuery(e.target.value)
          setActive(0)
        }}
        onKeyDown={(e) => {
          if (isImeConfirm(e.nativeEvent)) return
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            setActive((i) => Math.min(results.length - 1, i + 1))
          } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setActive((i) => Math.max(0, i - 1))
          } else if (e.key === 'Enter') {
            // 画面側のショートカット（レビューの Enter で開閉など）に届かせない
            e.preventDefault()
            e.stopPropagation()
            run(results[active])
          }
        }}
      />
      {results.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">見つかりません</p>
      ) : (
        // ARIA の combobox パターン。キーボードは入力欄が aria-activedescendant で受ける
        // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-to-interactive-role
        <ul id="command-results" role="listbox" className="max-h-80 overflow-y-auto p-1">
          {results.map((command, i) => (
            // oxlint-disable-next-line jsx-a11y/click-events-have-key-events
            <li
              key={command.id}
              id={`command-${command.id}`}
              // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-to-interactive-role
              role="option"
              aria-selected={i === active}
              className={cn(
                'flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm',
                i === active && 'bg-accent text-accent-foreground',
              )}
              onMouseEnter={() => setActive(i)}
              onClick={() => run(command)}
            >
              <span className="w-16 flex-none text-xs text-muted-foreground">{command.group}</span>
              {command.label}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
