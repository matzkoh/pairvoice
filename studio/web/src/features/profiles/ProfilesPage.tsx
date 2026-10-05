import { useSuspenseQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useRef, useState } from 'react'

import { PageHeader } from '@/components/PageHeader'
import { StatusText } from '@/components/StatusText'
import { useTransientStatus } from '@/lib/useTransientStatus'

import { healthQueryOptions } from '../health/queries'
import { NewProfile } from './NewProfile'
import { pairvoiceOutdated } from './pairvoiceOutdated'
import { ProfileCard } from './ProfileCard'
import { ProfileList } from './ProfileList'
import { type ProfileStatus, profilesQueryOptions } from './queries'
import { VoiceWorkbench } from './VoiceWorkbench'

// URL の ?id= で開くプロファイルを選ぶ。'new' は作成フォーム。省くと使用中のものを開く
export const NEW_PROFILE = 'new'

export function ProfilesPage() {
  const { data: profiles } = useSuspenseQuery(profilesQueryOptions())
  const { data: health } = useSuspenseQuery(healthQueryOptions())
  const { id } = useSearch({ from: '/profiles' })
  const navigate = useNavigate({ from: '/profiles' })
  const [rawStatus, setStatus] = useState<ProfileStatus | null>(null)
  const status = useTransientStatus(rawStatus)
  const audioRef = useRef<HTMLAudioElement>(null)

  const activeItem = profiles.items.find((item) => item.id === profiles.active)
  // 消された ID や古いブックマークは、使用中（無ければ先頭）に落とす
  const selected =
    id === NEW_PROFILE
      ? null
      : (profiles.items.find((item) => item.id === id) ?? activeItem ?? profiles.items[0] ?? null)

  function select(next: string | undefined) {
    void navigate({ search: { id: next } })
  }

  function play(url: string) {
    const player = audioRef.current
    if (!player) return
    player.src = url
    // autoplay 拒否など。読み込みの失敗は <audio> の onError で拾う
    void player.play().catch(() => {})
  }

  return (
    <div>
      <PageHeader
        title="プロファイル"
        description="プロファイルは参照音声（声そのもの）と caption（話し方の指示）の組です。試聴しながら caption を直せます。切り替えは次の読み上げから効き、再起動は要りません。"
      />
      {/* 知らせが消えても下がずれないよう、行の高さを先に取っておく。ずれると
          押そうとしたボタンの隣を押してしまう */}
      <div className="mb-3 min-h-4">
        {status && <StatusText message={status.message} isError={status.isError} />}
      </div>
      <div className="grid grid-cols-[14rem_minmax(0,1fr)] items-start gap-6">
        <ProfileList
          items={profiles.items}
          active={profiles.active}
          selected={selected?.id ?? null}
          onSelect={select}
          onCreate={() => select(NEW_PROFILE)}
        />
        {selected === null ? (
          <NewProfile
            initialCaption={activeItem?.caption ?? ''}
            anchorText={health.pairvoice?.tts.anchor_text ?? ''}
            sampler={health.pairvoice?.tts.sampler}
            pairvoiceDown={health.pairvoice === null}
            onPlay={play}
            onStatus={setStatus}
            onCreated={select}
          />
        ) : (
          // プロファイルを移ったら、試聴の入力とテイクは持ち越さない
          <div key={selected.id} className="space-y-6">
            <ProfileCard
              item={selected}
              isActive={selected.id === profiles.active}
              onPlay={play}
              onStatus={setStatus}
              onDeleted={() => select(undefined)}
            />
            <VoiceWorkbench
              profileId={selected.id}
              adoptedCaption={selected.caption}
              sampler={health.pairvoice?.tts.sampler}
              pairvoiceDown={health.pairvoice === null}
              outdated={pairvoiceOutdated(health.pairvoice)}
              onPlay={play}
              onStatus={setStatus}
            />
          </div>
        )}
      </div>
      <audio
        ref={audioRef}
        hidden
        // 合成は成功しても、ファイルが消えていれば再生は無言で終わる。鳴らない理由を出す
        onError={() => setStatus({ message: '音声を再生できませんでした', isError: true })}
      />
    </div>
  )
}
