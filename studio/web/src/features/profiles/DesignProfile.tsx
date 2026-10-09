import { Play } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { audioFileUrl } from '@/lib/speak'

import type { SamplerOverrides } from '../../../../shared/api-types'
import { Field } from './Field'
import { MASTER_STEPS, recordTakes, REFERENCE_TEXTS, synth } from './mixSynth'
import type { ProfileStatus } from './queries'
import { initialValues, MAX_CANDIDATES } from './samplerKnobs'
import { SaveAsProfile } from './SaveAsProfile'
import { useAnchoredText } from './useAnchoredText'
import { type Take, useTakes } from './useTakes'

type Props = {
  initialCaption: string
  // 候補に読ませる文。選んだ候補の音声がそのまま参照音声になる。pairvoice が申告する
  // （既定の声を自動で作るときと同じ文。止まっている間は空）
  anchorText: string
  sampler?: SamplerOverrides
  pairvoiceDown: boolean
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// caption だけで声を作ると、seed ごとに別人の声になる。候補を並べて聞き比べ、
// 気に入った1つの音声を参照音声として保存する（以後はその声でクローンするので揃う）
export function DesignProfile({
  initialCaption,
  anchorText,
  sampler,
  pairvoiceDown,
  onPlay,
  onStatus,
  onCreated,
}: Props) {
  const [caption, setCaption] = useState(initialCaption)
  const [text, setText] = useAnchoredText(anchorText)
  const [count, setCount] = useState(3)
  const { takes, running, start, stop } = useTakes({
    onReady: (relativePath) => onPlay(audioFileUrl(relativePath)),
  })
  const canGenerate = !running && !pairvoiceDown && caption.trim() !== '' && text.trim() !== ''

  function generate() {
    if (!canGenerate) return
    start({
      // サンプラーは config.toml の値のまま（seed だけテイクごとに振る）
      input: {
        text,
        caption: caption.trim(),
        values: initialValues(sampler),
        sampler: {},
        design: true,
      },
      count,
      seedBase: null,
    })
  }

  return (
    <div className="max-w-2xl space-y-5">
      <Field
        label="caption"
        htmlFor="design-caption"
        hint="話し方の指示。候補の声もこの描写から作り、プロファイルにもそのまま入ります。"
      >
        <Textarea
          id="design-caption"
          rows={3}
          value={caption}
          placeholder="落ち着いた若い女性の声。ややゆっくりと、丸い声で穏やかに話す。"
          onChange={(e) => setCaption(e.target.value)}
        />
      </Field>
      <Field
        label="参照音声"
        hint="caption から seed だけ変えた候補を作ります。選んだ候補の音声がそのまま参照音声になります。"
      >
        <div className="space-y-3 rounded-lg border bg-background p-3">
          <div>
            <label htmlFor="design-text" className="mb-1.5 block text-xs text-muted-foreground">
              候補に読ませる文
            </label>
            <Textarea
              id="design-text"
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-3">
            <label htmlFor="design-count" className="text-xs text-muted-foreground">
              候補数 <span className="font-mono text-foreground">{count}</span>
            </label>
            <input
              id="design-count"
              type="range"
              min={1}
              max={MAX_CANDIDATES}
              step={1}
              value={count}
              disabled={running}
              className="accent-primary"
              onChange={(e) => setCount(Number(e.target.value))}
            />
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              disabled={!running && !canGenerate}
              onClick={running ? stop : generate}
            >
              {running ? '中止' : '候補を作る'}
            </Button>
          </div>
          {pairvoiceDown && (
            <p className="text-xs text-destructive">pairvoice が止まっているので作れません。</p>
          )}
          {takes.length > 0 && (
            <ul aria-label="候補" className="space-y-2 border-t pt-3">
              {takes.map((take) => (
                <CandidateRow
                  key={take.id}
                  take={take}
                  onPlay={onPlay}
                  onStatus={onStatus}
                  onCreated={onCreated}
                />
              ))}
            </ul>
          )}
        </div>
      </Field>
    </div>
  )
}

type RowProps = {
  take: Take
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

function CandidateRow({ take, onPlay, onStatus, onCreated }: RowProps) {
  const relativePath = take.relativePath

  return (
    <li className="flex items-center gap-2">
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={`候補 #${take.id} を再生`}
        disabled={!relativePath}
        onClick={() => relativePath && onPlay(audioFileUrl(relativePath))}
      >
        <Play />
      </Button>
      <span className="text-sm tabular-nums">#{take.id}</span>
      <span className="font-mono text-xs text-muted-foreground">seed {take.seed}</span>
      {take.status === 'waiting' && <span className="text-xs text-muted-foreground">待機中</span>}
      {take.status === 'running' && <span className="text-xs text-muted-foreground">生成中…</span>}
      {take.status === 'error' && <span className="text-xs text-destructive">{take.message}</span>}
      {take.status === 'done' && relativePath && (
        <div className="ml-auto">
          <SaveAsProfile
            label={`候補 #${take.id}`}
            caption={take.input.caption}
            // 選んだテイクを参照にして、同じ声で別の文も読ませる（テイクを複製した声なので、
            // テイクそのものとはわずかに違う）
            record={(progress) =>
              recordTakes(
                REFERENCE_TEXTS,
                (text) =>
                  synth(
                    text,
                    take.input.caption,
                    take.seed,
                    [{ audio: relativePath, weight: 1 }],
                    MASTER_STEPS,
                  ),
                progress,
                [relativePath],
              )
            }
            onStatus={onStatus}
            onCreated={onCreated}
          />
        </div>
      )}
    </li>
  )
}
