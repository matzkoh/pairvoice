import { Plus, Search } from 'lucide-react'
import { useRef, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Kbd } from '@/components/ui/kbd'
import type { ProfileItem, ProfileSource } from '@/lib/api-types'
import { isImeConfirm, useHotkeys } from '@/lib/hotkeys'

import { nextSelection } from '../review/selection'
import { ORDER_LABELS } from './MemberPicker'

export const SOURCE_LABELS: Record<ProfileSource, string> = {
  design: '作った声',
  upload: '取り込み',
  curate: '素材から',
  auto: '自動で作成',
  import: '設定から移行',
  random: 'ランダム',
}

// 一覧の見出しの順。ランダムの声はほかの声を束ねるものなので先頭に置く
const SOURCE_ORDER: readonly ProfileSource[] = [
  'random',
  'design',
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

// 行の2行目。ランダムの声は caption を持たないので、選び方とメンバーを出す
function detail(item: ProfileItem, names: ReadonlyMap<string, string>): string {
  if (item.source !== 'random') return item.caption
  return `${ORDER_LABELS[item.order]}: ${item.members.map((id) => names.get(id) ?? id).join('・')}`
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
  const groups = SOURCE_ORDER.map((source) => ({
    source,
    items: matches.filter((item) => item.source === source),
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
      // 一覧だけを送れるようにする。ページごと送ると、下の方の声を選ぶたびに中身が画面の外へ出る。
      // 高さは開いた時点で見出しの下に収まる分（ページの見出しと知らせの行で約 11rem）
      className="sticky top-4 flex max-h-[calc(100dvh-11rem)] flex-col gap-2"
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
          variant={selected === null ? 'secondary' : 'outline'}
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
          <li key={group.source}>
            <h3 className="sticky top-0 z-10 flex items-baseline justify-between bg-background px-3 pt-3 pb-1 text-xs font-medium text-muted-foreground">
              {SOURCE_LABELS[group.source]}
              <span className="tabular-nums">{group.items.length}</span>
            </h3>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const sub = detail(item, names)
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      ref={item.id === selected ? scrollIntoView : undefined}
                      aria-current={item.id === selected ? 'true' : undefined}
                      title={item.name}
                      className="w-full rounded-md px-3 py-1.5 text-left transition-colors hover:bg-muted aria-[current]:bg-muted"
                      onClick={() => onSelect(item.id)}
                    >
                      <span className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{item.name}</span>
                        {item.id === active && <Badge className="ml-auto shrink-0">使用中</Badge>}
                      </span>
                      {sub && (
                        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                          {sub}
                        </span>
                      )}
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
      <p className="flex items-center gap-1 px-3 text-xs text-muted-foreground">
        <Kbd>/</Kbd>絞り込む
        <Kbd className="ml-2">J</Kbd>
        <Kbd>K</Kbd>上下に移る
      </p>
    </nav>
  )
}
