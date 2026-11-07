import { Button } from '@/components/ui/button'
import type { ProfileItem, ProfileOrder } from '@/lib/api-types'

// 1つだけでは選ぶ意味が無い（pairvoice も 400 で断る）
export const MIN_MEMBERS = 2

// ランダムの声のメンバーにできる声。ランダムの声は入れ子にできない
export function memberCandidates(items: readonly ProfileItem[]): ProfileItem[] {
  return items.filter((item) => item.source !== 'random')
}

const ORDERS: readonly ProfileOrder[] = ['random', 'shuffle']

export const ORDER_LABELS: Record<ProfileOrder, string> = {
  random: 'ランダム',
  shuffle: 'シャッフル',
}

const ORDER_HINTS: Record<ProfileOrder, string> = {
  random: '毎回ばらばらに選ぶ。同じ声が続くこともある',
  shuffle: '混ぜた順に、全員を一巡するまで同じ声を選ばない',
}

type OrderProps = {
  value: ProfileOrder
  disabled?: boolean
  onChange: (next: ProfileOrder) => void
}

export function OrderPicker({ value, disabled, onChange }: OrderProps) {
  return (
    <div role="radiogroup" aria-label="選び方" className="space-y-1">
      {ORDERS.map((order) => (
        <label key={order} className="flex items-baseline gap-2 text-sm">
          <input
            type="radio"
            checked={value === order}
            disabled={disabled}
            onChange={() => onChange(order)}
            className="size-3.5 accent-primary"
          />
          {ORDER_LABELS[order]}
          <span className="text-xs text-muted-foreground">{ORDER_HINTS[order]}</span>
        </label>
      ))}
    </div>
  )
}

type PickerProps = {
  voices: readonly ProfileItem[]
  value: readonly string[]
  disabled?: boolean
  onChange: (next: string[]) => void
}

export function MemberPicker({ voices, value, disabled, onChange }: PickerProps) {
  const all = voices.map((voice) => voice.id)
  // 消した声の ID が残っていることがあるので、value.length ではなく今ある声で数える
  const chosen = all.filter((id) => value.includes(id)).length
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {chosen} / {all.length} を選択
        </span>
        <Button
          variant="ghost"
          size="xs"
          className="ml-auto"
          disabled={disabled || chosen === all.length}
          onClick={() => onChange(all)}
        >
          すべて選ぶ
        </Button>
        <Button
          variant="ghost"
          size="xs"
          disabled={disabled || chosen === 0}
          onClick={() => onChange([])}
        >
          すべて外す
        </Button>
      </div>
      {/* 声が増えても縦に伸びすぎないよう、幅に合わせて段を組む */}
      <ul
        aria-label="メンバー"
        className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-1"
      >
        {voices.map((voice) => (
          <li key={voice.id}>
            <label
              title={voice.name}
              className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors hover:bg-muted has-checked:border-primary/40 has-checked:bg-primary/5"
            >
              <input
                type="checkbox"
                checked={value.includes(voice.id)}
                disabled={disabled}
                onChange={(e) =>
                  onChange(
                    e.target.checked ? [...value, voice.id] : value.filter((id) => id !== voice.id),
                  )
                }
                className="size-3.5 shrink-0 accent-primary"
              />
              <span className="truncate">{voice.name}</span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  )
}
