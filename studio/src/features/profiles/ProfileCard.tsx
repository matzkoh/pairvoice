import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState, useTransition } from 'react'

import { ConfirmButton } from '@/components/ConfirmButton'
import { PlayButton } from '@/components/PlayButton'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { VoiceMark } from '@/components/VoiceMark'
import { apiSend, apiUrl, toErrorMessage } from '@/lib/api'
import type { ProfileItem, ProfileOrder } from '@/lib/api-types'
import { isImeConfirm } from '@/lib/hotkeys'

import { HOLDINGS, holdingOf, holdingText } from './holding'
import { eligibleMembers, MemberPicker, MIN_MEMBERS, OrderPicker } from './MemberPicker'
import { SOURCE_LABELS } from './ProfileList'
import { invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  item: ProfileItem
  // ランダムの声のメンバーにできる声
  voices: readonly ProfileItem[]
  isActive: boolean
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onDeleted: () => void
}

// 選んだプロファイルの中身（参照音声 + caption、ランダムの声はメンバー）と、名前・使用中・削除の操作
export function ProfileCard({ item, voices, isActive, onPlay, onStatus, onDeleted }: Props) {
  const queryClient = useQueryClient()
  const [pending, startTransition] = useTransition()
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(item.name)
  // 1回の編集で確定か取り消しのどちらか1度だけ。入力欄が消えるときにブラウザが blur を
  // 送ってくるので、Enter の後に二重に保存し、Escape の後にも保存してしまう
  const editing = useRef(false)
  const path = `/profiles/${encodeURIComponent(item.id)}`

  function run(work: () => Promise<unknown>, done: string, after?: () => void) {
    startTransition(async () => {
      try {
        await work()
        await invalidateProfiles(queryClient)
        onStatus({ message: done, isError: false })
        after?.()
      } catch (err: unknown) {
        onStatus({ message: toErrorMessage(err), isError: true })
      }
    })
  }

  function startRename() {
    editing.current = true
    setRenaming(true)
  }

  function rename() {
    if (!editing.current) return
    editing.current = false
    const next = name.trim()
    setRenaming(false)
    if (next === '' || next === item.name) {
      setName(item.name)
      return
    }
    run(() => apiSend(path, 'PATCH', { name: next }), '名前を変えました')
  }

  return (
    <section aria-label="プロファイルの中身">
      <div className="flex min-h-12 flex-wrap items-center gap-x-4 gap-y-2">
        <VoiceMark
          id={item.id}
          members={item.source === 'random' ? item.members : undefined}
          className="size-12"
        />
        <div className="min-w-0 flex-1 basis-48">
          {renaming ? (
            <Input
              aria-label="プロファイルの名前"
              className="h-9 max-w-80 font-voice text-lg"
              value={name}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              onBlur={rename}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !isImeConfirm(e.nativeEvent)) rename()
                if (e.key === 'Escape') {
                  editing.current = false
                  setName(item.name)
                  setRenaming(false)
                }
              }}
            />
          ) : (
            <button
              type="button"
              className="block max-w-full truncate text-left font-voice text-2xl leading-9 decoration-input decoration-dashed underline-offset-4 outline-none hover:underline focus-visible:underline"
              title="クリックで名前を変える"
              onClick={startRename}
            >
              {item.name}
            </button>
          )}
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            {SOURCE_LABELS[item.source]}
            {isActive && <span className="marked font-medium text-foreground">使用中</span>}
          </p>
        </div>
        {/* 使用中を消すと、次の読み上げで既定の声が黙って作り直されるので削除も出さない */}
        {!isActive && (
          <div className="flex items-center gap-2">
            <ConfirmButton
              variant="ghost"
              idle="削除"
              armed="本当に削除"
              disabled={pending}
              onConfirm={() =>
                run(() => apiSend(path, 'DELETE'), `「${item.name}」を削除しました`, onDeleted)
              }
            />
            <Button
              disabled={pending}
              onClick={() =>
                run(
                  () => apiSend('/profiles/active', 'PUT', { id: item.id }),
                  `「${item.name}」を使います（次の読み上げから効きます）`,
                )
              }
            >
              使う
            </Button>
          </div>
        )}
      </div>
      {item.source === 'random' ? (
        <MembersEditor
          item={item}
          voices={voices}
          pending={pending}
          onSave={(change) =>
            run(
              () => apiSend(path, 'PATCH', change),
              'メンバーと選び方を変えました（次の読み上げから効きます）',
            )
          }
        />
      ) : (
        <dl className="mt-5 grid grid-cols-[5rem_minmax(0,1fr)] items-center gap-x-4 gap-y-3">
          <dt className="self-start text-xs leading-5 text-muted-foreground">持ち方</dt>
          <dd>
            <HoldingNote item={item} />
          </dd>
          <dt className="text-xs text-muted-foreground">参照音声</dt>
          <dd className="flex items-start gap-3">
            <PlayButton
              voice={item.id}
              aria-label={`${item.name} の参照音声を再生`}
              onClick={() => onPlay(apiUrl(`${path}/audio`))}
            />
            {item.folded && (
              <p className="max-w-[52em] text-xs text-muted-foreground">
                素材の中心に近い 26
                秒の見本で、つなぎ目で言葉が途切れます。声は素材全体から要約したものなので、下の試聴で確かめてください。
              </p>
            )}
          </dd>
          {item.mix.length > 0 && (
            <>
              <dt className="text-xs text-muted-foreground">もとの声</dt>
              <dd className="text-sm">
                {/* 混ぜた表現は保存してあるので、もとの声を消しても読める */}
                {item.mix
                  .map(
                    (part) =>
                      `${voices.find((voice) => voice.id === part.id)?.name ?? '（削除した声）'} ${Math.round(part.weight * 100)}%`,
                  )
                  .join(' / ')}
              </dd>
            </>
          )}
          <dt className="self-start text-xs leading-[1.6rem] text-muted-foreground">caption</dt>
          <dd className="max-w-[52em] text-sm whitespace-pre-wrap">
            {item.caption || <span className="text-muted-foreground">（なし）</span>}
          </dd>
        </dl>
      )}
    </section>
  )
}

type MembersProps = {
  item: ProfileItem
  voices: readonly ProfileItem[]
  pending: boolean
  onSave: (change: { members: string[]; order: ProfileOrder }) => void
}

function MembersEditor({ item, voices, pending, onSave }: MembersProps) {
  const candidates = eligibleMembers(voices, item.id)
  const [members, setMembers] = useState<string[]>(item.members)
  const [order, setOrder] = useState<ProfileOrder>(item.order)
  const changed =
    order !== item.order ||
    members.length !== item.members.length ||
    members.some((id) => !item.members.includes(id))

  return (
    <div className="mt-5 space-y-4">
      <p className="text-xs text-muted-foreground">
        読み上げのたびに、この中から1つを選んで読みます。caption と口調はその声のものを使います。
      </p>
      <MemberPicker voices={candidates} value={members} disabled={pending} onChange={setMembers} />
      <OrderPicker value={order} disabled={pending} onChange={setOrder} />
      <Button
        disabled={pending || !changed || members.length < MIN_MEMBERS}
        onClick={() => onSave({ members, order })}
      >
        保存
      </Button>
    </div>
  )
}

// 声の持ち方と、その良し悪し。表現を保存した声が今のモデルと合わないときは、写した声で
// 読んでいると知らせる（黙って近い声に替わっている）
function HoldingNote({ item }: { item: ProfileItem }) {
  const holding = holdingOf(item)
  if (item.reads_from_reference) {
    return (
      <div className="max-w-[52em] text-sm">
        <p className="font-medium text-destructive">参照音声から写して読んでいます</p>
        <p className="text-xs text-muted-foreground">
          別の合成モデル（{item.speaker_model}
          ）で保存した声なので、今のモデルでは参照音声から写した近い声で読みます。聞いた声のまま読みたいなら、今のモデルで作り直してください。
        </p>
      </div>
    )
  }
  return (
    <div className="max-w-[52em] text-sm">
      <p className="font-medium">{HOLDINGS[holding].label}</p>
      <p className="text-xs text-muted-foreground">
        {holdingText(holding)}
        {holding === 'speaker' &&
          '下の参照音声は、合成モデルを替えたときの写し元で、ふだんの読み上げには使いません。'}
      </p>
    </div>
  )
}
