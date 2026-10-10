import { useSuspenseQuery } from '@tanstack/react-query'
import { cn } from 'cn'

import { MuteMenu } from './MuteMenu'
import { type StatusTone, pairvoiceStatus } from './pairvoiceStatus'
import { healthQueryOptions } from './queries'

const TONE_BG: Record<StatusTone, string> = {
  ok: 'bg-success',
  warn: 'bg-warning',
  bad: 'bg-destructive',
}

// 色は当たりを付けるための補助で、読ませたいのは理由の文字列（読み上げが来ない理由の切り分け）
export function StatusFooter() {
  const { data } = useSuspenseQuery(healthQueryOptions())
  const { tone, label } = pairvoiceStatus(data)
  return (
    <div className="flex flex-col gap-1 border-t border-sidebar-border px-2 pt-3 text-xs text-muted-foreground">
      <div className="flex items-center gap-2" title={label}>
        <span className={cn('size-2 flex-none rounded-full', TONE_BG[tone])} aria-hidden="true" />
        <span className="truncate">{label}</span>
      </div>
      <MuteMenu disabled={!data} />
    </div>
  )
}
