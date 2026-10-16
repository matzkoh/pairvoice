import { cn } from 'cn'
import { FileAudio } from 'lucide-react'

import { buttonVariants } from '@/components/ui/button'

type Props = {
  label: string
  accept: string
  multiple?: boolean
  // 選んだ後に空へ戻すには input を作り直すしかない（value を書き換えられない）。変えると作り直す
  resetKey?: number
  summary: string
  ariaLabel?: string
  onPick: (files: File[]) => void
}

// ボタンの見た目のラベルで、隠した file input を開く
export function FilePicker({
  label,
  accept,
  multiple,
  resetKey,
  summary,
  ariaLabel,
  onPick,
}: Props) {
  return (
    <div className="flex items-center gap-3">
      <label
        className={cn(
          buttonVariants({ variant: 'outline', size: 'sm' }),
          // 本体の input は隠れているので、キーボードで当たったときの印をラベルに出す
          'cursor-pointer has-focus-visible:ring-3 has-focus-visible:ring-ring/50',
        )}
      >
        <FileAudio />
        {label}
        <input
          key={resetKey}
          type="file"
          multiple={multiple}
          accept={accept}
          className="sr-only"
          aria-label={ariaLabel}
          onChange={(e) => onPick(Array.from(e.target.files ?? []))}
        />
      </label>
      <span className="truncate text-sm text-muted-foreground">{summary}</span>
    </div>
  )
}
