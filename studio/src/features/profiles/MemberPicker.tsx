import type { ProfileItem } from '@/lib/api-types'

// 1つだけでは選ぶ意味が無い（pairvoice も 400 で断る）
export const MIN_MEMBERS = 2

// ランダムの声のメンバーにできる声。ランダムの声は入れ子にできない
export function memberCandidates(items: readonly ProfileItem[]): ProfileItem[] {
  return items.filter((item) => item.source !== 'random')
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
