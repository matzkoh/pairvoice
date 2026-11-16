import { useQueryClient } from '@tanstack/react-query'
import { LoaderCircle } from 'lucide-react'
import { useRef, useState, useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { apiSend, toErrorMessage } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'
import { audioFileUrl } from '@/lib/speak'

import { Field } from './Field'
import { memberCandidates } from './MemberPicker'
import { mixWeight } from './mixCurve'
import { mixQueryOptions } from './mixSynth'
import { randomSeed } from './PickProfile'
import { invalidateProfiles, type ProfileStatus } from './queries'
import { useAnchoredText } from './useAnchoredText'

type Props = {
  voices: readonly ProfileItem[]
  // 試聴に読ませる文。pairvoice が申告する（止まっている間は空）
  anchorText: string
  pairvoiceDown: boolean
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// スライダーの目盛りの数
const STEPS = 100
// キーで動かしたとき、止まってから試聴を頼むまでの間
const SETTLE_MS = 400

// 試聴の1回分。頼んだ時点の入力をまとめて持ち、合成の最中に書き換えた入力を混ぜない
type Listen = { a: string; b: string; caption: string; text: string; position: number }

// 位置の声の重み。B の重みは位置を聞こえ方にそろえた値
function voiceMix(a: string, b: string, w: number) {
  return [
    { voice: a, weight: 1 - w },
    { voice: b, weight: w },
  ]
}

// 2つの声の話者の表現を混ぜた声を作る。スライダーを離すたびに試聴し、気に入った位置で保存する
export function MixProfile({
  voices,
  anchorText,
  pairvoiceDown,
  onPlay,
  onStatus,
  onCreated,
}: Props) {
  const queryClient = useQueryClient()
  const candidates = memberCandidates(voices)
  const [a, setA] = useState('')
  const [b, setB] = useState('')
  const [caption, setCaption] = useState('')
  const [text, setText] = useAnchoredText(anchorText)
  const [position, setPosition] = useState(STEPS / 2)
  // 種は画面を開いている間は変えない。位置を動かしたときに、声以外（間や抑揚）が変わらないように
  const [seed] = useState(randomSeed)
  const [name, setName] = useState('')
  const [saving, startSave] = useTransition()
  const dragging = useRef(false)
  const timer = useRef<number | undefined>(undefined)

  const nameA = candidates.find((voice) => voice.id === a)?.name
  const nameB = candidates.find((voice) => voice.id === b)?.name
  const ready = nameA !== undefined && nameB !== undefined && a !== b && text.trim() !== ''

  // 合成は1件ずつにする。合成の最中に頼まれた分は最後の1件だけ残し、終わってから頼む
  // （積むと Runner を塞ぎ、フックの読み上げが待たされる）。鳴らすのも最後の1件だけ
  const [synthesizing, setSynthesizing] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const busy = useRef(false)
  const next = useRef<Listen | null>(null)

  async function listen(first: Listen) {
    next.current = first
    if (busy.current) return
    busy.current = true
    setSynthesizing(true)
    try {
      while (next.current) {
        const { a: va, b: vb, caption: c, text: t, position: at } = next.current
        next.current = null
        // 同じ入力の音は取っておいたものを使う（前に聞いた位置に戻ったときは合成しない）
        const made = await queryClient.query(
          mixQueryOptions(t, c, seed, voiceMix(va, vb, mixWeight(at / STEPS))),
        )
        if (!next.current) onPlay(audioFileUrl(made.relative_path))
      }
      setFailure(null)
    } catch (err: unknown) {
      next.current = null
      setFailure(toErrorMessage(err))
    } finally {
      busy.current = false
      setSynthesizing(false)
    }
  }

  function request(at: number) {
    if (!ready || pairvoiceDown) return
    void listen({ a, b, caption: caption.trim(), text, position: at })
  }

  // ドラッグ中は頼まず、離したときに1回だけ頼む。キーは止まってから頼む
  function move(at: number, settle: boolean) {
    setPosition(at)
    window.clearTimeout(timer.current)
    if (settle) request(at)
    else if (!dragging.current) timer.current = window.setTimeout(() => request(at), SETTLE_MS)
  }

  const w = mixWeight(position / STEPS)

  function save() {
    if (!ready || name.trim() === '') return
    startSave(async () => {
      try {
        const created = await apiSend<ProfileItem>('/profiles/mix', 'POST', {
          name: name.trim(),
          caption: caption.trim(),
          voices: voiceMix(a, b, w),
          rng_seed: seed,
        })
        await invalidateProfiles(queryClient)
        onStatus({ message: `「${created.name}」を作りました`, isError: false })
        setName('')
        onCreated(created.id)
      } catch (err: unknown) {
        onStatus({ message: `作れませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  const selectClass =
    'h-9 w-full rounded-md border bg-transparent px-2 text-sm shadow-xs disabled:opacity-50'

  return (
    <div className="max-w-2xl space-y-5">
      <div className="grid grid-cols-2 gap-4">
        {(
          [
            ['A', a, setA],
            ['B', b, setB],
          ] as const
        ).map(([label, value, set]) => (
          <Field key={label} label={`声 ${label}`} htmlFor={`mix-${label}`}>
            <select
              id={`mix-${label}`}
              className={selectClass}
              value={value}
              disabled={saving}
              onChange={(e) => set(e.target.value)}
            >
              <option value="">選ぶ</option>
              {candidates.map((voice) => (
                <option key={voice.id} value={voice.id}>
                  {voice.name}
                </option>
              ))}
            </select>
          </Field>
        ))}
      </div>
      <Field
        label="混ぜ具合"
        htmlFor="mix-position"
        hint="離すと、その位置の声で読みます。矢印キーで細かく動かせます。もとの声どうしが似ていると、動かしても違いはわずかです。"
      >
        <input
          id="mix-position"
          type="range"
          min={0}
          max={STEPS}
          step={1}
          value={position}
          disabled={!ready || saving}
          className="w-full accent-primary"
          onPointerDown={() => {
            dragging.current = true
          }}
          // 離したことが届かないと、以後キーで動かしても試聴を頼まなくなる
          onPointerCancel={() => {
            dragging.current = false
          }}
          onPointerUp={(e) => {
            dragging.current = false
            move(e.currentTarget.valueAsNumber, true)
          }}
          onChange={(e) => move(e.currentTarget.valueAsNumber, false)}
        />
        <div className="flex justify-between text-xs text-muted-foreground tabular-nums">
          <span>
            {nameA ?? 'A'} {Math.round((1 - w) * 100)}%
          </span>
          {synthesizing && (
            <span role="status" className="flex items-center gap-1">
              <LoaderCircle className="size-3 animate-spin" />
              合成しています
            </span>
          )}
          <span>
            {nameB ?? 'B'} {Math.round(w * 100)}%
          </span>
        </div>
        {failure && <p className="text-xs text-destructive">試聴できませんでした: {failure}</p>}
      </Field>
      <Field
        label="caption"
        htmlFor="mix-caption"
        hint="話し方の指示。空なら caption なしで読みます。試聴と同じ caption で保存します。"
      >
        <Textarea
          id="mix-caption"
          rows={2}
          value={caption}
          disabled={saving}
          onChange={(e) => setCaption(e.target.value)}
        />
      </Field>
      <Field label="試聴の文" htmlFor="mix-text">
        <Textarea
          id="mix-text"
          rows={2}
          value={text}
          disabled={saving}
          onChange={(e) => setText(e.target.value)}
        />
      </Field>
      <Button
        variant="outline"
        disabled={!ready || pairvoiceDown || saving}
        onClick={() => request(position)}
      >
        この位置で試聴
      </Button>
      <Field
        label="名前"
        htmlFor="mix-name"
        hint="混ぜた声で決まった文を3つ読ませて参照音声も作るので、30 秒ほどかかります。"
      >
        <Input
          id="mix-name"
          value={name}
          disabled={saving}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Button disabled={!ready || pairvoiceDown || saving || name.trim() === ''} onClick={save}>
        {saving && <LoaderCircle className="animate-spin" />}
        プロファイルを作る
      </Button>
    </div>
  )
}
