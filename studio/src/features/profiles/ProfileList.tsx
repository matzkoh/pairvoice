import { cn } from 'cn'
import { Plus, Search } from 'lucide-react'
import { useRef, useState } from 'react'

import { LIST_ROW } from '@/components/ListDetail'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Kbd } from '@/components/ui/kbd'
import { VoiceMark } from '@/components/VoiceMark'
import type { ProfileItem, ProfileSource } from '@/lib/api-types'
import { isImeConfirm, useHotkeys } from '@/lib/hotkeys'

import { nextSelection } from '../review/selection'
import { HOLDING_ORDER, HOLDINGS, holdingOf, holdingText } from './holding'
import { ORDER_LABELS } from './MemberPicker'

export const SOURCE_LABELS: Record<ProfileSource, string> = {
  design: '作った声',
  upload: '取り込み',
  curate: '素材から',
  auto: '自動で作成',
  import: '設定から移行',
  random: 'ランダム',
  mix: '混ぜた声',
}

// 見出し（持ち方）の中の並び。同じ作り方の声をまとめる
const SOURCE_ORDER: readonly ProfileSource[] = [
  'random',
  'design',
  'mix',
  'curate',
  'upload',
  'auto',
  'import',
]

type Props = {
  items: readonly ProfileItem[]
  active: string | null
  // 選んでいるプロファイルの ID。作成フォームを開いているときは null
  selected: string | null
  onSelect: (id: string) => void
  onCreate: () => void
}

// 行の2行目。見出しは持ち方なので、作り方をここに添える。ランダムの声は caption を
// 持たないので、選び方とメンバーを出す
function detail(item: ProfileItem, names: ReadonlyMap<string, string>): string {
  if (item.source === 'random') {
    return `${ORDER_LABELS[item.order]}: ${item.members.map((id) => names.get(id) ?? id).join('・')}`
  }
  const source = SOURCE_LABELS[item.source]
  return item.caption ? `${source} · ${item.caption}` : source
}

// 選んだ行が一覧の外にあれば見える所まで送る（使用中が下の方にあっても開いた時に見える）。
// 選ぶ行が変わったときだけ付け替わるので、そのたびに1度だけ呼ばれる
function scrollIntoView(row: HTMLElement | null) {
  row?.scrollIntoView?.({ block: 'nearest' })
}

export function ProfileList({ items, active, selected, onSelect, onCreate }: Props) {
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const names = new Map(items.map((item) => [item.id, item.name]))
  const trimmed = query.trim()
  const q = trimmed.toLowerCase()
  // ランダムの声はメンバーの名前では引かない。メンバーで探したときに束ねた側が先頭に来て、Enter で開いてしまう
  const matches = items.filter((item) => `${item.name}\n${item.caption}`.toLowerCase().includes(q))
  const sorted = SOURCE_ORDER.flatMap((source) => matches.filter((item) => item.source === source))
  const groups = HOLDING_ORDER.map((holding) => ({
    holding,
    items: sorted.filter((item) => holdingOf(item) === holding),
  })).filter((group) => group.items.length > 0)
  // 見出しで並べ替えた後の順。j / k はこの順に動く
  const visible = groups.flatMap((group) => group.items)

  function move(delta: 1 | -1) {
    const next = nextSelection(
      visible.map((item) => item.id),
      selected,
      delta,
    )
    if (next !== null && next !== selected) onSelect(next)
  }

  useHotkeys({
    j: () => move(1),
    k: () => move(-1),
    '/': () => searchRef.current?.focus(),
  })

  return (
    <nav
      aria-label="プロファイル一覧"
      // 絞り込みと新規作成は上に残し、行だけを送る
      className="flex min-h-0 flex-1 flex-col gap-2"
    >
      <div className="flex items-center gap-1.5">
        <div className="relative min-w-0 flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            ref={searchRef}
            type="search"
            aria-label="プロファイルを絞り込む"
            placeholder={`${items.length}件から絞り込む`}
            className="pl-8"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setQuery('')
              if (e.key === 'Enter' && !isImeConfirm(e.nativeEvent) && visible[0]) {
                onSelect(visible[0].id)
              }
            }}
          />
        </div>
        <Button
          variant={selected === null ? 'default' : 'outline'}
          size="icon"
          aria-label="新しく作る"
          aria-current={selected === null ? 'true' : undefined}
          title="新しく作る"
          onClick={onCreate}
        >
          <Plus />
        </Button>
      </div>
      <ul className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        {groups.map((group) => (
          <li key={group.holding}>
            <h3
              className="sticky top-0 z-10 flex items-baseline justify-between bg-background px-2 pt-3 pb-1 text-xs font-medium text-muted-foreground"
              title={holdingText(group.holding)}
            >
              {HOLDINGS[group.holding].label}
              <span>{group.items.length}</span>
            </h3>
            <ul className="space-y-px">
              {group.items.map((item) => {
                const sub = detail(item, names)
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      ref={item.id === selected ? scrollIntoView : undefined}
                      aria-current={item.id === selected ? 'true' : undefined}
                      title={item.name}
                      className={cn(LIST_ROW, 'flex items-center gap-2.5 px-2 py-1.5')}
                      onClick={() => onSelect(item.id)}
                    >
                      <VoiceMark
                        id={item.id}
                        members={item.source === 'random' ? item.members : undefined}
                        className="size-7"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate font-voice">{item.name}</span>
                          <span className="ml-auto flex flex-none items-center gap-1.5 text-[11px] font-medium">
                            {item.muffled && <span className="text-destructive">こもり</span>}
                            {item.id === active && <span className="marked">使用中</span>}
                          </span>
                        </span>
                        {sub && (
                          <span className="block truncate text-xs text-muted-foreground">
                            {sub}
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </li>
        ))}
        {visible.length === 0 && (
          <li className="px-3 py-6 text-center text-xs text-muted-foreground">
            「{trimmed}」に合うプロファイルはありません
          </li>
        )}
      </ul>
      <p className="flex items-center gap-1 px-2 text-xs text-muted-foreground max-md:hidden">
        <Kbd>/</Kbd>絞り込む
        <Kbd className="ml-2">J</Kbd>
        <Kbd>K</Kbd>上下に移る
      </p>
    </nav>
  )
}
