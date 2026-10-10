import { useRef } from 'react'

import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { Textarea } from '@/components/ui/textarea'

import { EmojiPalette } from './EmojiPalette'
import { SamplerFields } from './SamplerFields'
import { type KnobValue, MAX_CANDIDATES, type SamplerKey, type SamplerValues } from './samplerKnobs'
import { useEmojiInsert } from './useEmojiInsert'

const COUNT_HINT = `seed だけを変えて何件作るか。まとめて1回で作らず1件ずつ順に合成する（合成は1本のキューを通るので、まとめると読み上げが長く待たされる）。上限 ${MAX_CANDIDATES} 件`

type Props = {
  text: string
  onTextChange: (text: string) => void
  onPickFromCorpus: () => void
  caption: string
  onCaptionChange: (caption: string) => void
  // プロファイルに保存されている caption
  adoptedCaption: string
  values: SamplerValues
  onKnobChange: (key: SamplerKey, value: KnobValue) => void
  errors: readonly string[]
  count: number
  onCountChange: (count: number) => void
  running: boolean
  canGenerate: boolean
  onGenerate: () => void
  onStop: () => void
}

export function ConditionsPanel(p: Props) {
  const textRef = useRef<HTMLTextAreaElement>(null)
  const insertEmoji = useEmojiInsert(textRef, p.text, p.onTextChange)
  const captionEdited = p.caption !== p.adoptedCaption

  return (
    <div className="sticky top-0 space-y-4 rounded-lg border bg-card p-4">
      <div>
        <div className="mb-1.5 flex items-center">
          <label htmlFor="voice-text" className="text-xs text-muted-foreground">
            テキスト
          </label>
          <Button
            variant="link"
            size="sm"
            className="ml-auto h-auto p-0 text-xs"
            onClick={p.onPickFromCorpus}
          >
            実際の読み上げから選ぶ
          </Button>
        </div>
        <Textarea
          id="voice-text"
          ref={textRef}
          rows={3}
          value={p.text}
          onChange={(e) => p.onTextChange(e.target.value)}
        />
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            絵文字で演技を指示
          </summary>
          <div className="mt-2">
            <EmojiPalette onInsert={insertEmoji} />
          </div>
        </details>
      </div>
      <div>
        <div className="mb-1.5 flex items-center gap-2">
          <label htmlFor="voice-caption" className="text-xs text-muted-foreground">
            caption
          </label>
          <span className="text-xs text-muted-foreground">
            {captionEdited ? '保存済みから変更' : '保存済み'}
          </span>
          {captionEdited && (
            <Button
              variant="link"
              size="sm"
              className="ml-auto h-auto p-0 text-xs"
              onClick={() => p.onCaptionChange(p.adoptedCaption)}
            >
              保存済みに戻す
            </Button>
          )}
        </div>
        <Textarea
          id="voice-caption"
          rows={3}
          value={p.caption}
          onChange={(e) => p.onCaptionChange(e.target.value)}
        />
        <p className="mt-1 text-xs text-muted-foreground">
          空にすると caption なし（参照音声だけ）で読みます。
        </p>
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">サンプラー</summary>
        <div className="mt-3">
          <SamplerFields values={p.values} onChange={p.onKnobChange} disabled={p.running} />
        </div>
      </details>
      {p.errors.length > 0 && (
        <ul className="space-y-0.5 text-xs text-destructive">
          {p.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-3">
        <label htmlFor="voice-count" className="text-xs text-muted-foreground" title={COUNT_HINT}>
          候補数 <span className="font-mono text-foreground">{p.count}</span>
        </label>
        <input
          id="voice-count"
          type="range"
          min={1}
          max={MAX_CANDIDATES}
          step={1}
          value={p.count}
          disabled={p.running}
          onChange={(e) => p.onCountChange(Number(e.target.value))}
          className="flex-1 accent-primary"
        />
      </div>
      {p.running ? (
        <Button variant="outline" className="w-full" onClick={p.onStop}>
          残りを中止
        </Button>
      ) : (
        <Button className="w-full" disabled={!p.canGenerate} onClick={p.onGenerate}>
          生成 <Kbd>⌘↵</Kbd>
        </Button>
      )}
    </div>
  )
}
