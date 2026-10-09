import { useQuery } from '@tanstack/react-query'
import { useEffect, useEffectEvent, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { apiSend, toErrorMessage } from '@/lib/api'
import { speakOnce } from '@/lib/speak'

import { BestVoice } from './BestVoice'
import { Field } from './Field'
import { PickRound } from './PickRound'
import { ProgressBar } from './ProgressBar'
import type { ProfileStatus } from './queries'
import { RetryNote } from './RetryNote'
import { SpreadSparkline } from './SpreadSparkline'
import { Survey } from './Survey'
import { useAnchoredText } from './useAnchoredText'
import { seedsFor } from './useTakes'
import { AXES, captionFor, FEMALE, type Point, randomPoint } from './voiceAxes'
import { VoiceMap } from './VoiceMap'
import {
  answer,
  type Choice,
  mapOf,
  mixOf,
  regionOf,
  roundOf,
  type Side,
  spreadsOf,
  type Step,
  type Voice,
  voiceOf,
  withVoices,
} from './voiceRegion'

type Props = {
  anchorText: string
  pairvoiceDown: boolean
  // 鳴らす（プロファイルの画面の再生を使う）
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

function randomSeed() {
  return seedsFor(null, 1)[0]!
}

// 始める前に作る声の数。答えた軸だけの caption の声と、残りの軸をでたらめにした声2本
export const FIRST_VOICES = 3

type Session = { known: Point; seed: number }

// 2択を繰り返して声を絞り込む。言葉で描写しなくても、聞き比べて近い方を選ぶだけで
// 望みの声に近づく。候補の範囲をはっきり違う2つの声で半分ずつに切り、その真ん中を
// 「見つけた声」にする（しくみは voiceRegion）
export function PickProfile({ anchorText, pairvoiceDown, onPlay, onStatus, onCreated }: Props) {
  const [text, setText] = useAnchoredText(anchorText)
  // 事前アンケート。言葉にしやすい軸（性別・年代など）は先に答えてもらう。null は文の入力画面
  const [survey, setSurvey] = useState<{ at: number; known: Point } | null>(null)
  const [session, setSession] = useState<Session | null>(null)

  function begin(known: Point) {
    setSurvey(null)
    setSession({ known, seed: randomSeed() })
  }

  if (session) {
    return (
      <Search
        session={session}
        text={text.trim()}
        onReset={() => setSession(null)}
        onPlay={onPlay}
        onStatus={onStatus}
        onCreated={onCreated}
      />
    )
  }

  if (survey) {
    return (
      <Survey
        at={survey.at}
        female={survey.known.gender === FEMALE}
        onAnswer={(level) => {
          const known = { ...survey.known, [AXES[survey.at]!.key]: level }
          if (survey.at + 1 < AXES.length) setSurvey({ at: survey.at + 1, known })
          else begin(known)
        }}
        onBack={() => setSurvey(survey.at > 0 ? { ...survey, at: survey.at - 1 } : null)}
        onSkipRest={() => begin(survey.known)}
      />
    )
  }

  return (
    <div className="max-w-2xl space-y-5">
      <Field label="候補に読ませる文" htmlFor="pick-text">
        <Textarea id="pick-text" rows={2} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      <Button
        disabled={pairvoiceDown || text.trim() === ''}
        onClick={() => setSurvey({ at: 0, known: {} })}
      >
        始める
      </Button>
      {pairvoiceDown && (
        <p className="text-xs text-destructive">pairvoice が止まっているので作れません。</p>
      )}
    </div>
  )
}

type SearchProps = {
  session: Session
  text: string
  onReset: () => void
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// 手持ちの声を1本作って測る。事前の質問で答えた軸はそろえ、答えていない軸をばらばらに
// した caption で作る（答えた範囲の中で、どの向きにも振れるように）
async function makeVoice(text: string, known: Point, signal?: AbortSignal) {
  const take = await speakOnce(
    {
      text,
      caption: captionFor(randomPoint(known)),
      sampler: { rng_seed: randomSeed() },
      design: true,
    },
    signal,
  )
  return voiceOf(take.relative_path, await measure(take.relative_path, signal))
}

async function measure(path: string, signal?: AbortSignal) {
  const { vector } = await apiSend<{ vector: number[] }>(
    '/api/speaker-vector',
    'POST',
    { audio: path },
    { signal },
  )
  return vector
}

function Search({ session, text, onReset, onPlay, onStatus, onCreated }: SearchProps) {
  const [progress, setProgress] = useState(0)
  // 始める前の声は1人ずつ順に作って測る（まとめると Runner を塞ぎ、フックの読み上げが
  // 待たされる）。1人目は答えた軸だけの caption、残りは答えていない軸をでたらめにした声。画面を離れたら
  // 残りは投げない
  const prepared = useQuery({
    queryKey: ['pick-first', text, session] as const,
    queryFn: async ({ signal }) => {
      const take = await speakOnce(
        {
          text,
          caption: captionFor(session.known),
          sampler: { rng_seed: session.seed },
          design: true,
        },
        signal,
      )
      const first = voiceOf(take.relative_path, await measure(take.relative_path, signal))
      setProgress(1)
      const second = await makeVoice(text, session.known, signal)
      setProgress(2)
      const third = await makeVoice(text, session.known, signal)
      setProgress(3)
      return [first, second, third]
    },
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })

  if (prepared.isError) {
    return (
      <div className="max-w-2xl space-y-3">
        <p className="text-sm text-destructive">
          声の候補を作れませんでした: {toErrorMessage(prepared.error)}
        </p>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void prepared.refetch()}>
            もう一度
          </Button>
          <Button variant="ghost" size="sm" onClick={onReset}>
            最初から
          </Button>
        </div>
      </div>
    )
  }

  if (!prepared.data) {
    return (
      <div className="max-w-2xl space-y-3">
        <p className="text-sm">
          声の候補を用意しています（{progress} / {FIRST_VOICES}）
        </p>
        <ProgressBar value={progress / FIRST_VOICES} />
        <Button variant="ghost" size="sm" onClick={onReset}>
          やめる
        </Button>
      </div>
    )
  }

  return (
    <Climb
      voices={prepared.data}
      session={session}
      text={text}
      onReset={onReset}
      onPlay={onPlay}
      onStatus={onStatus}
      onCreated={onCreated}
    />
  )
}

type ClimbProps = Omit<SearchProps, 'session'> & {
  session: Session
  // 始める前に作った声（手持ちの声のはじめ）
  voices: readonly Voice[]
}

// 手持ちの声を何本まで増やすか。1問聞いている間に1本ずつ作ってアンケートの範囲を埋める
// （まとめて作ると Runner を塞ぎ、次の問いの A と B やフックの読み上げが待たされる）
const MAX_VOICES = 24

function Climb({
  voices: firstVoices,
  session,
  text,
  onReset,
  onPlay,
  onStatus,
  onCreated,
}: ClimbProps) {
  const [voices, setVoices] = useState<readonly Voice[]>(firstVoices)
  const [steps, setSteps] = useState<readonly Step[]>(() => {
    const region = regionOf(firstVoices, session.seed)
    return [{ region, round: roundOf(region) }]
  })
  // A と B ができた問いの番号。聞いている間に、手持ちの声を増やす
  const [readyRound, setReadyRound] = useState<number | null>(null)
  const [voiceError, setVoiceError] = useState<string | null>(null)
  const [playing, setPlaying] = useState<Side | null>(null)
  const current = steps.at(-1)!
  const map = useMemo(() => mapOf(steps), [steps])
  const spreads = useMemo(() => spreadsOf(steps), [steps])
  const mix = useMemo(() => {
    const { region, round } = current
    return {
      a: mixOf(region.voices, round.a),
      b: mixOf(region.voices, round.b),
      center: mixOf(region.voices, round.center),
    }
  }, [current])
  const caption = captionFor(session.known)

  function onAnswer(choice: Choice) {
    // 前の問いの音は止まる（作り直しで <audio> ごと外れ、pause が届かないことがある）
    setPlaying(null)
    const region = withVoices(answer(current.region, current.round, choice), voices)
    setSteps([...steps, { region, round: roundOf(region) }])
  }

  function onUndo() {
    setPlaying(null)
    setSteps(steps.slice(0, -1))
  }

  // 手持ちの声を先回りして増やす。A と B を聞いている間だけ、1問に1本作り、A と B の合成の
  // 前に割り込まない。画面を離れたら待っている要求ごと打ち切る
  const target = Math.min(MAX_VOICES, firstVoices.length + steps.length)
  const wanted =
    voiceError === null && readyRound === steps.length && voices.length < target
      ? voices.length
      : null
  const grow = useEffectEvent(async (signal: AbortSignal) => {
    try {
      const voice = await makeVoice(text, session.known, signal)
      setVoices((prev) => [...prev, voice])
    } catch (err: unknown) {
      if (!signal.aborted) setVoiceError(toErrorMessage(err))
    }
  })
  useEffect(() => {
    if (wanted === null) return undefined
    const controller = new AbortController()
    // grow が状態を書き換えるのは await の後だけ（lint は非同期の流れを追えない）
    // oxlint-disable-next-line react/set-state-in-effect
    void grow(controller.signal)
    return () => controller.abort()
  }, [wanted])

  return (
    <div className="max-w-2xl space-y-5">
      <PickRound
        // 1問ごとに作り直し、名前の入力や保存済みの印を次の問いへ持ち越さない。地図は外に
        // 置いて作り直さず、問いのあいだの動きを見せる
        key={steps.length}
        mix={mix}
        index={steps.length}
        text={text}
        caption={caption}
        seed={session.seed}
        onAnswer={onAnswer}
        onReady={() => setReadyRound(steps.length)}
        playing={playing}
        onPlayingChange={setPlaying}
        onUndo={steps.length > 1 ? onUndo : undefined}
        onReset={onReset}
        onStatus={onStatus}
      />
      {voiceError && (
        <RetryNote
          message={`聞き比べに使う声を作れませんでした: ${voiceError}`}
          onRetry={() => setVoiceError(null)}
        />
      )}
      <BestVoice
        narrowed={1 - spreads.at(-1)!}
        onPlay={onPlay}
        mix={mix.center}
        text={text}
        caption={caption}
        seed={session.seed}
        onStatus={onStatus}
        onCreated={onCreated}
      />
      <div className="space-y-3 border-t pt-3">
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>絞り込み</span>
          <SpreadSparkline spreads={spreads} />
        </div>
        <VoiceMap map={map} playing={playing} />
      </div>
    </div>
  )
}
