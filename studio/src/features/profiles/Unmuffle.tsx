import { queryOptions, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircle } from 'lucide-react'
import { useState, useTransition } from 'react'

import { PlayButton } from '@/components/PlayButton'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { apiSend, toErrorMessage } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'
import { audioFileUrl, speakOnce } from '@/lib/speak'

import { createdStatus, invalidateProfiles, type ProfileStatus } from './queries'

// どれだけ足せば澄んで聞こえるかは声によるので、前後を聴いて選んでもらう。標準は、こもった声で
// 耳で選んだ強さ。強いほど声も変わる（上限はサーバーが 4 で止める）
const STRENGTHS = [
  { label: '弱め', amount: 2 },
  { label: '標準', amount: 3 },
  { label: '強め', amount: 4 },
] as const
// こもりやすい文で聴き比べる
const PREVIEW_TEXT = '設定ファイルを読み直したので、次の読み上げから新しい声に切り替わります。'
const PREVIEW_SEED = 1

// 強さごとの試聴。前後で同じ文・同じ種にして、強さの違いだけを聴かせる
function previewQuery(item: ProfileItem, amount: number, enabled: boolean) {
  return queryOptions({
    queryKey: ['unmuffle', item.id, amount] as const,
    // 強さを切り替えた・閉じたら、作りかけの試聴は打ち切る（Runner に溜めない）
    queryFn: async ({ signal }) =>
      (
        await speakOnce(
          {
            text: PREVIEW_TEXT,
            caption: item.caption,
            sampler: { rng_seed: PREVIEW_SEED },
            mix: [{ voice: item.id, weight: 1 }],
            unmuffle: amount,
          },
          signal,
        )
      ).relative_path,
    staleTime: Infinity,
    retry: false,
    enabled,
  })
}

type Props = {
  item: ProfileItem
  // いまの合成モデルの、こもりの向きを同梱しているか
  available: boolean
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// こもった声（上の帯域が欠けた声）の知らせと、直した声を別の声として保存する操作。
// 元の声は書き換えない（直した声が気に入らなければ、元の声のまま使い続けられる）
export function Unmuffle({ item, available, onPlay, onStatus, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [amount, setAmount] = useState<number>(STRENGTHS[1].amount)
  const [name, setName] = useState(`${item.name}（クリア）`)
  const [saving, startSave] = useTransition()
  // 前を作り終えてから後を作る（同時に頼むと Runner を塞ぎ、フックの読み上げが待たされる）
  const before = useQuery(previewQuery(item, 0, open))
  const after = useQuery(previewQuery(item, amount, open && before.isSuccess))
  const failed = before.error ?? after.error

  function save() {
    if (name.trim() === '') return
    startSave(async () => {
      try {
        const created = await apiSend<ProfileItem>('/profiles/mix', 'POST', {
          name: name.trim(),
          caption: item.caption,
          voices: [{ voice: item.id, weight: 1 }],
          unmuffle: amount,
        })
        await invalidateProfiles(queryClient)
        onStatus(createdStatus(created, '保存しました'))
        onCreated(created.id)
      } catch (err: unknown) {
        onStatus({ message: `保存できませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  return (
    <div className="max-w-[52em] space-y-3 text-sm">
      <div>
        <p className="font-medium text-destructive">こもった音です</p>
        <p className="text-xs text-muted-foreground">
          上の帯域が欠けています。こもりを取った声を別の声として保存できます（元の声はそのまま残ります）。
          {!available && 'いまの合成モデルでは直せません。'}
        </p>
      </div>
      {available && !open && (
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          こもりを取る
        </Button>
      )}
      {open && (
        <div className="space-y-3 rounded-lg border p-3">
          <div role="radiogroup" aria-label="強さ" className="flex gap-1">
            {STRENGTHS.map((strength) => (
              <Button
                key={strength.label}
                role="radio"
                aria-checked={amount === strength.amount}
                variant={amount === strength.amount ? 'default' : 'outline'}
                size="sm"
                onClick={() => setAmount(strength.amount)}
              >
                {strength.label}
              </Button>
            ))}
          </div>
          <div className="flex items-center gap-4">
            <Take label="前" path={before.data} onPlay={onPlay} />
            <Take label="後" path={after.data} voice={item.id} onPlay={onPlay} />
          </div>
          {failed && (
            <p className="text-xs text-destructive">
              試聴を作れませんでした: {toErrorMessage(failed)}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="直した声の名前"
              className="h-8 max-w-72"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Button variant="ghost" size="sm" disabled={saving} onClick={() => setOpen(false)}>
              やめる
            </Button>
            <Button size="sm" disabled={saving || name.trim() === ''} onClick={save}>
              {saving && <LoaderCircle className="size-3.5 animate-spin" />}
              別の声として保存
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

// 試聴の1本。作っている間は回る印を出す
function Take({
  label,
  path,
  voice,
  onPlay,
}: {
  label: string
  path: string | undefined
  voice?: string
  onPlay: (url: string) => void
}) {
  return (
    <span className="flex items-center gap-2">
      {path === undefined ? (
        <LoaderCircle
          role="status"
          aria-label={`${label}を作っています`}
          className="size-4 animate-spin text-muted-foreground"
        />
      ) : (
        <PlayButton
          voice={voice}
          aria-label={`${label}を再生`}
          onClick={() => onPlay(audioFileUrl(path))}
        />
      )}
      {label}
    </span>
  )
}
