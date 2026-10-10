import { cn } from 'cn'
import { Plus } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import type { ProfileItem, ProfileSource } from '@/lib/api-types'

export const SOURCE_LABELS: Record<ProfileSource, string> = {
  design: '作った声',
  upload: '取り込み',
  auto: '自動で作成',
  import: '設定から移行',
}

type Props = {
  items: readonly ProfileItem[]
  active: string | null
  // 選んでいるプロファイルの ID。作成フォームを開いているときは null
  selected: string | null
  onSelect: (id: string) => void
  onCreate: () => void
}

const ROW = 'w-full rounded-md px-3 py-2 text-left transition-colors hover:bg-muted'

export function ProfileList({ items, active, selected, onSelect, onCreate }: Props) {
  return (
    <nav aria-label="プロファイル一覧" className="space-y-1">
      <ul className="space-y-1">
        {items.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              aria-current={item.id === selected ? 'true' : undefined}
              className={cn(ROW, 'aria-[current]:bg-muted')}
              onClick={() => onSelect(item.id)}
            >
              <span className="flex items-center gap-2">
                <span className="truncate text-sm font-medium">{item.name}</span>
                {item.id === active && <Badge className="ml-auto">使用中</Badge>}
              </span>
              <span className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                {item.caption}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        aria-current={selected === null ? 'true' : undefined}
        className={cn(
          ROW,
          'flex items-center gap-1.5 text-sm text-muted-foreground aria-[current]:bg-muted aria-[current]:text-foreground',
        )}
        onClick={onCreate}
      >
        <Plus className="size-4" aria-hidden="true" />
        新しく作る
      </button>
    </nav>
  )
}
