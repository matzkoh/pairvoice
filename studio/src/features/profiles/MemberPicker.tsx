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
  return (
    <ul aria-label="メンバー" className="space-y-1">
      {voices.map((voice) => (
        <li key={voice.id}>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={value.includes(voice.id)}
              disabled={disabled}
              onChange={(e) =>
                onChange(
                  e.target.checked ? [...value, voice.id] : value.filter((id) => id !== voice.id),
                )
              }
              className="size-3.5 accent-primary"
            />
            {voice.name}
          </label>
        </li>
      ))}
    </ul>
  )
}
