import { useState } from 'react'

import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'
import { apiGet, toErrorMessage } from '@/lib/api'
import { useHotkeys } from '@/lib/hotkeys'
import { audioFileUrl } from '@/lib/speak'

import type { CorpusResponse, SamplerOverrides } from '../../../../shared/api-types'
import { CaptionHistorySheet } from './CaptionHistorySheet'
import { ConditionsPanel } from './ConditionsPanel'
import type { ProfileStatus } from './queries'
import { type KnobValue, type SamplerKey, initialValues, toSamplerPayload } from './samplerKnobs'
import { TakeCard } from './TakeCard'
import { useTakes } from './useTakes'

const DEFAULT_TEXT = 'やった、テスト全部通ったよ。あとはレビューだね'

type Props = {
  // 試聴で鳴らすプロファイル。テイクはこの参照音声でクローンし、採用はこの caption に書く
  profileId: string
  adoptedCaption: string
  sampler?: SamplerOverrides
  pairvoiceDown: boolean
  outdated: boolean
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
}

// プロファイルの caption を書き換えながら試聴する。試聴では保存済みの caption を
// 書き換えず、良いテイクの caption だけを「採用」でプロファイルに書く
export function VoiceWorkbench({
  profileId,
  adoptedCaption,
  sampler,
  pairvoiceDown,
  outdated,
  onPlay,
  onStatus,
}: Props) {
  const [text, setText] = useState(DEFAULT_TEXT)
  const [caption, setCaption] = useState(adoptedCaption)
  const [values, setValues] = useState(() => initialValues(sampler))
  const [count, setCount] = useState(1)
  const [errors, setErrors] = useState<string[]>([])
  const { takes, running, start, stop, retry, clear } = useTakes({
    onReady: (relativePath) => onPlay(audioFileUrl(relativePath)),
  })

  // 保存済みの caption が変わったとき（採用・復元）、書き換えていない入力欄は追従させる。
  // 書き換え中の入力欄は触らない
  const [syncedAdopted, setSyncedAdopted] = useState(adoptedCaption)
  if (adoptedCaption !== syncedAdopted) {
    if (caption === syncedAdopted) setCaption(adoptedCaption)
    setSyncedAdopted(adoptedCaption)
  }

  const canGenerate = !running && !pairvoiceDown && text.trim() !== ''

  function generate() {
    if (!canGenerate) return
    const { sampler: payload, errors: parseErrors } = toSamplerPayload(values)
    setErrors(parseErrors)
    if (parseErrors.length > 0) return
    // seed はテイクごとに振るので、フォームの rng_seed は起点としてだけ使う
    const { rng_seed: seedBase, ...rest } = payload
    start({
      input: { text, caption: caption.trim(), values, sampler: rest, profileId },
      count,
      seedBase: seedBase ?? null,
    })
  }

  useHotkeys({ 'mod+enter': generate })

  async function pickFromCorpus() {
    try {
      const corpus = await apiGet<CorpusResponse>('/corpus?limit=1')
      const latest = corpus.items[0]
      if (!latest) {
        onStatus({ message: '読み上げの記録がまだありません', isError: true })
        return
      }
      setText(latest.summary)
    } catch (err: unknown) {
      onStatus({ message: `読み上げを取れませんでした: ${toErrorMessage(err)}`, isError: true })
    }
  }

  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <h2 className="text-sm font-medium">試聴して caption を直す</h2>
        <div className="ml-auto">
          <CaptionHistorySheet profileId={profileId} />
        </div>
      </div>
      {pairvoiceDown && (
        <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          pairvoice が止まっているので生成できません。
        </p>
      )}
      {outdated && (
        <p className="mb-3 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-sm text-warning">
          pairvoice が古く、サンプラーを動かしても反映されません。uv run pairvoice restart
          で再起動してください。
        </p>
      )}
      <div className="grid grid-cols-[minmax(0,22rem)_minmax(0,1fr)] items-start gap-6">
        <ConditionsPanel
          text={text}
          onTextChange={setText}
          onPickFromCorpus={() => void pickFromCorpus()}
          caption={caption}
          onCaptionChange={setCaption}
          adoptedCaption={adoptedCaption}
          values={values}
          onKnobChange={(key: SamplerKey, value: KnobValue) =>
            setValues((prev) => ({ ...prev, [key]: value }))
          }
          errors={errors}
          count={count}
          onCountChange={setCount}
          running={running}
          canGenerate={canGenerate}
          onGenerate={generate}
          onStop={stop}
        />
        <section aria-label="テイク">
          <div className="mb-2 flex items-center">
            <h3 className="text-xs font-medium text-muted-foreground">
              テイク <span className="tabular-nums">{takes.length}</span>
            </h3>
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto"
              disabled={running || takes.length === 0}
              onClick={clear}
            >
              クリア
            </Button>
          </div>
          {takes.length === 0 ? (
            <EmptyState>左で条件を組んで「生成」を押すと、ここにテイクが溜まります。</EmptyState>
          ) : (
            <ul className="space-y-2">
              {takes.map((take) => (
                <TakeCard
                  key={take.id}
                  take={take}
                  profileId={profileId}
                  adoptedCaption={adoptedCaption}
                  canRetry={!running && !pairvoiceDown}
                  onPlay={() => take.relativePath && onPlay(audioFileUrl(take.relativePath))}
                  onRetry={() => retry(take.id)}
                  onAdopted={onStatus}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
