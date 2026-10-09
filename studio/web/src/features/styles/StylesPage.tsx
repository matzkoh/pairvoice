import { useSuspenseQuery } from '@tanstack/react-query'
import { cn } from 'cn'
import { Plus } from 'lucide-react'
import { useRef, useState } from 'react'

import { PageHeader } from '@/components/PageHeader'
import { StatusText } from '@/components/StatusText'
import { useTransientStatus } from '@/lib/useTransientStatus'

import { healthQueryOptions } from '../health/queries'
import { type ProfileStatus, profilesQueryOptions } from '../profiles/queries'
import { stylesQueryOptions } from './queries'
import { StyleEditor } from './StyleEditor'

const ROW = 'w-full rounded-md px-3 py-2 text-left transition-colors hover:bg-muted'

export function StylesPage() {
  const { data } = useSuspenseQuery(stylesQueryOptions())
  const { data: profiles } = useSuspenseQuery(profilesQueryOptions())
  const { data: health } = useSuspenseQuery(healthQueryOptions())
  // 選んでいるスタイルの名前。null は作成フォーム
  const [selectedName, setSelectedName] = useState<string | null>(() => data.items[0]?.name ?? null)
  const [rawStatus, setStatus] = useState<ProfileStatus | null>(null)
  const status = useTransientStatus(rawStatus)
  const audioRef = useRef<HTMLAudioElement>(null)

  const selected = data.items.find((s) => s.name === selectedName) ?? null

  function play(url: string) {
    const player = audioRef.current
    if (!player) return
    player.src = url
    void player.play().catch(() => {})
  }

  return (
    <div>
      <PageHeader
        title="スタイル"
        description="caption（話し方の指示）とサンプラーの組に名前を付けて保存しておき、読み上げごとに選べます。API の /speak に style で名前を渡すと、そのリクエストだけこの caption とサンプラーで読みます。変えられるのは速さ・テンション・抑揚の傾きまでで、ささやき声のような声質は参照音声で決まるので変わりません。声質を変えたいときは voice で別のプロファイルを選んでください。"
      />
      <div className="mb-3 min-h-4">
        {status && <StatusText message={status.message} isError={status.isError} />}
      </div>
      <div className="grid grid-cols-[14rem_minmax(0,1fr)] items-start gap-6">
        <nav aria-label="スタイル一覧" className="space-y-1">
          <ul className="space-y-1">
            {data.items.map((style) => (
              <li key={style.name}>
                <button
                  type="button"
                  aria-current={style.name === selected?.name ? 'true' : undefined}
                  className={cn(ROW, 'aria-[current]:bg-muted')}
                  onClick={() => setSelectedName(style.name)}
                >
                  <span className="block truncate text-sm font-medium">{style.name}</span>
                  <span className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                    {style.caption ?? 'プロファイルの caption のまま'}
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
            onClick={() => setSelectedName(null)}
          >
            <Plus className="size-4" aria-hidden="true" />
            新しく作る
          </button>
        </nav>
        {/* スタイルを移ったら、編集中の値は持ち越さない */}
        <StyleEditor
          key={selected?.name ?? ''}
          style={selected}
          styles={data.items}
          profiles={profiles}
          baseSampler={health.pairvoice?.tts.sampler}
          pairvoiceDown={health.pairvoice === null}
          onPlay={play}
          onStatus={setStatus}
          onSaved={setSelectedName}
          onDeleted={() => setSelectedName(null)}
        />
      </div>
      <audio
        ref={audioRef}
        hidden
        onError={() => setStatus({ message: '音声を再生できませんでした', isError: true })}
      />
    </div>
  )
}
