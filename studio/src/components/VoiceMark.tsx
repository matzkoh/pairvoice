import { cn } from 'cn'

import { VOICE_BARS, voiceBars, voiceStyle } from '@/lib/voiceColor'

type Props = {
  // 声の ID。無い（古い記録など、どの声か分からない）ときは色を付けない
  id: string | null | undefined
  // 束ねた声（ランダム）のメンバー。棒をメンバーの色で塗り分ける
  members?: readonly string[]
  className?: string
}

const SIZE = 24
const PAD = 5
const BAR = 2.4
const GAP = (SIZE - PAD * 2 - BAR * VOICE_BARS) / (VOICE_BARS - 1)
const MAX = 14

// 声の印。色と棒の形で誰の声かの当たりが付く（確かめるのは名前。声が増えると色は近いものが出る）。
// 飾りなので読み上げには出さず、名前は呼び出し側が文字で添える
export function VoiceMark({ id, members, className }: Props) {
  const bars = voiceBars(id ?? '')
  return (
    <svg
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      aria-hidden="true"
      className={cn('size-6 flex-none', id ? 'text-voice' : 'text-muted-foreground', className)}
      style={voiceStyle(id)}
    >
      <rect width={SIZE} height={SIZE} rx={7} fill="currentColor" opacity={0.15} />
      {bars.map((height, i) => {
        const h = height * MAX
        const member = members && members.length > 0 ? members[i % members.length] : undefined
        return (
          <rect
            // 棒は並び順でしか区別しない
            // oxlint-disable-next-line react/no-array-index-key
            key={i}
            x={PAD + i * (BAR + GAP)}
            y={(SIZE - h) / 2}
            width={BAR}
            height={h}
            rx={BAR / 2}
            fill="currentColor"
            className={member ? 'text-voice' : undefined}
            style={voiceStyle(member)}
          />
        )
      })}
    </svg>
  )
}
