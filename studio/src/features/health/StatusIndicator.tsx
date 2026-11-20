import { useQuery, useSuspenseQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { cn } from 'cn'

import { VoiceMark } from '@/components/VoiceMark'

import { profilesQueryOptions } from '../profiles/queries'
import { MuteMenu } from './MuteMenu'
import { type StatusTone, pairvoiceStatus } from './pairvoiceStatus'
import { healthQueryOptions } from './queries'

const TONE_BG: Record<StatusTone, string> = {
  ok: 'bg-success',
  warn: 'bg-warning',
  bad: 'bg-destructive',
}

// いま読む声と、読み上げが来ない理由。色は当たりを付けるための補助で、読ませたいのは
// 理由の文字列（読み上げが来ない理由の切り分け）。正常なときは文字を出さず、点だけにする
export function StatusIndicator() {
  const { data } = useSuspenseQuery(healthQueryOptions())
  const { tone, label } = pairvoiceStatus(data)
  const voice = data?.tts.profile
  // 束ねた声（ランダム）のメンバーは health に無いので一覧から引く。読めるまでは1色で描く
  const { data: profiles } = useQuery({
    ...profilesQueryOptions(),
    enabled: voice?.source === 'random',
  })
  const members = profiles?.items.find((item) => item.id === voice?.id)?.members
  return (
    <>
      {voice && (
        <Link
          to="/profiles"
          search={{ id: voice.id }}
          title={`いま読む声: ${voice.name}`}
          className="flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 hover:bg-accent max-md:hidden"
        >
          <VoiceMark id={voice.id} members={members} className="size-5" />
          <span className="truncate font-voice text-[13px]">{voice.name}</span>
        </Link>
      )}
      <div className="flex min-w-0 items-center gap-1.5 px-1.5 text-xs" title={label}>
        <span className={cn('size-2 flex-none rounded-full', TONE_BG[tone])} aria-hidden="true" />
        <span className={cn('truncate', tone === 'ok' && 'sr-only')}>{label}</span>
      </div>
      <MuteMenu disabled={!data} />
    </>
  )
}
