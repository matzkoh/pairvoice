import { cn } from 'cn'
import { Play } from 'lucide-react'
import type { ComponentProps } from 'react'

import { voiceStyle } from '@/lib/voiceColor'

type Props = Omit<ComponentProps<'button'>, 'children'> & {
  // 鳴らす声。分かっていれば声の色で塗る（一覧で、どの声の読み上げかが色で分かる）
  voice?: string | null
  size?: 'sm' | 'md'
}

const SIZES = {
  sm: 'size-7 rounded-lg [&_svg]:size-3',
  md: 'size-8 rounded-[10px] [&_svg]:size-3.5',
}

// 音を鳴らすボタン。この道具でいちばん押すものなので、ほかの操作と形を変えて見つけやすくする
export function PlayButton({ voice, size = 'md', className, style, ...props }: Props) {
  return (
    <button
      type="button"
      {...props}
      style={{ ...voiceStyle(voice), ...style }}
      className={cn(
        'grid flex-none place-items-center transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-default disabled:opacity-35',
        voice
          ? 'bg-voice/15 text-voice enabled:hover:bg-voice/25'
          : 'bg-muted text-foreground enabled:hover:bg-input/60',
        SIZES[size],
        className,
      )}
    >
      <Play fill="currentColor" strokeWidth={0} aria-hidden="true" />
    </button>
  )
}
