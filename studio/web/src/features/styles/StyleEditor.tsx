import { useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { useState, useTransition } from 'react'

import { ConfirmButton } from '@/components/ConfirmButton'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Kbd } from '@/components/ui/kbd'
import { Textarea } from '@/components/ui/textarea'
import { apiSend, toErrorMessage } from '@/lib/api'
import { useHotkeys } from '@/lib/hotkeys'
import { audioFileUrl, speakOnce } from '@/lib/speak'

import type { ProfilesResponse, SamplerOverrides, StyleData } from '../../../../shared/api-types'
import type { ProfileStatus } from '../profiles/queries'
import { SamplerFields } from '../profiles/SamplerFields'
import { toSamplerPayload } from '../profiles/samplerKnobs'
import { invalidateStyles } from './queries'
import { samplerDiff, styleValues } from './styleSampler'

const DEFAULT_TEXT = 'やった、テスト全部通ったよ。あとはレビューだね'

type Props = {
  // 直すスタイル。null は新しく作る
  style: StyleData | null
  styles: readonly StyleData[]
  profiles: ProfilesResponse
  // pairvoice がいま渡しているサンプラー。スタイルはこれと違う値の項目だけを持つ
  baseSampler?: SamplerOverrides
  pairvoiceDown: boolean
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onSaved: (name: string) => void
  onDeleted: () => void
}

export function StyleEditor({
  style,
  styles,
  profiles,
  baseSampler,
  pairvoiceDown,
  onPlay,
  onStatus,
  onSaved,
  onDeleted,
}: Props) {
  const queryClient = useQueryClient()
  const [name, setName] = useState(style?.name ?? '')
  // null は「声のプロファイルの caption のまま読む」。欄の文字は切り替えても残しておく。
  // 新しく作るときはこちらから始める（空の caption は「caption なしで読む」になってしまう）
  const [keepProfileCaption, setKeepProfileCaption] = useState(
    style ? style.caption === null : true,
  )
  const [caption, setCaption] = useState(style?.caption ?? '')
  const [values, setValues] = useState(() => styleValues(baseSampler, style?.sampler ?? {}))
  const [voice, setVoice] = useState(profiles.active ?? profiles.items[0]?.id ?? '')
  const [text, setText] = useState(DEFAULT_TEXT)
  const [errors, setErrors] = useState<string[]>([])
  const [trying, startTry] = useTransition()
  const [saving, startSave] = useTransition()

  const trimmedName = name.trim()
  const nameTaken = styles.some((s) => s.name === trimmedName && s.name !== style?.name)
  const canTry = !trying && !pairvoiceDown && voice !== '' && text.trim() !== ''
  // 保存する項目を config.toml との差で決めるので、いまの値が取れないうちは保存しない
  const canSave = !saving && !pairvoiceDown && trimmedName !== '' && !nameTaken

  function tryStyle() {
    if (!canTry) return
    const { sampler, errors: parseErrors } = toSamplerPayload(values)
    setErrors(parseErrors)
    if (parseErrors.length > 0) return
    startTry(async () => {
      try {
        const result = await speakOnce({
          text,
          ...(keepProfileCaption ? {} : { caption: caption.trim() }),
          sampler,
          profile_id: voice,
        })
        onPlay(audioFileUrl(result.relative_path))
      } catch (err: unknown) {
        onStatus({ message: `試聴できませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  useHotkeys({ 'mod+enter': tryStyle })

  function write(next: StyleData[], message: string, done: () => void) {
    startSave(async () => {
      try {
        await apiSend('/api/styles', 'PUT', { styles: next })
        await invalidateStyles(queryClient)
        onStatus({ message, isError: false })
        done()
      } catch (err: unknown) {
        onStatus({ message: `保存に失敗しました: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  function save() {
    if (!canSave) return
    const { sampler, errors: parseErrors } = samplerDiff(values, baseSampler)
    setErrors(parseErrors)
    if (parseErrors.length > 0) return
    const saved: StyleData = {
      name: trimmedName,
      caption: keepProfileCaption ? null : caption.trim(),
      sampler,
    }
    const next = style ? styles.map((s) => (s.name === style.name ? saved : s)) : [...styles, saved]
    write(next, `「${trimmedName}」を保存しました`, () => onSaved(trimmedName))
  }

  function remove() {
    if (!style) return
    write(
      styles.filter((s) => s.name !== style.name),
      `「${style.name}」を消しました`,
      onDeleted,
    )
  }

  return (
    <div className="space-y-5">
      {pairvoiceDown && (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          pairvoice が止まっているので、試聴と保存はできません。
        </p>
      )}
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,20rem)] items-start gap-6">
        <div className="space-y-4">
          <div>
            <label htmlFor="style-name" className="mb-1.5 block text-xs text-muted-foreground">
              名前（API の style に渡す値）
            </label>
            <Input
              id="style-name"
              value={name}
              placeholder="ささやき"
              onChange={(e) => setName(e.target.value)}
            />
            {nameTaken && (
              <p className="mt-1 text-xs text-destructive">同じ名前のスタイルがあります</p>
            )}
          </div>
          <div>
            <div className="mb-1.5 flex items-center gap-3">
              <label htmlFor="style-caption" className="text-xs text-muted-foreground">
                caption
              </label>
              <label className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={keepProfileCaption}
                  onChange={(e) => setKeepProfileCaption(e.target.checked)}
                  className="size-3.5 accent-primary"
                />
                声のプロファイルの caption のまま読む
              </label>
            </div>
            <Textarea
              id="style-caption"
              rows={3}
              value={caption}
              disabled={keepProfileCaption}
              placeholder="ささやくように、小さな声でゆっくり話す。"
              onChange={(e) => setCaption(e.target.value)}
            />
            <p className="mt-1 text-xs text-muted-foreground">
              プロファイルの caption
              の代わりに使います。よく効くのは「ゆっくり」「早口に」のような速さと、テンションの指示です。
            </p>
          </div>
          <details open={Object.keys(style?.sampler ?? {}).length > 0}>
            <summary className="cursor-pointer text-xs text-muted-foreground">
              サンプラー（変えた項目だけをスタイルに保存し、ほかは config.toml の値で読みます）
            </summary>
            <div className="mt-3">
              <SamplerFields
                values={values}
                onChange={(key, value) => setValues((prev) => ({ ...prev, [key]: value }))}
                disabled={saving}
              />
            </div>
          </details>
          {errors.length > 0 && (
            <ul className="space-y-0.5 text-xs text-destructive">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2">
            <Button disabled={!canSave} onClick={save}>
              保存
            </Button>
            {style && (
              <ConfirmButton
                idle="消す"
                armed="もう一度押すと消します"
                disabled={saving}
                onConfirm={remove}
              />
            )}
          </div>
        </div>
        <section aria-label="試聴" className="space-y-3 rounded-lg border bg-card p-4">
          <div>
            <label htmlFor="style-voice" className="mb-1.5 block text-xs text-muted-foreground">
              声
            </label>
            <select
              id="style-voice"
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              className="h-8 w-full rounded-md border bg-transparent px-2 text-sm"
            >
              {profiles.items.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.id === profiles.active ? '（使用中）' : ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="style-text" className="mb-1.5 block text-xs text-muted-foreground">
              テキスト
            </label>
            <Textarea
              id="style-text"
              rows={3}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <Button variant="outline" className="w-full" disabled={!canTry} onClick={tryStyle}>
            <Play />
            {trying ? '合成しています…' : '試聴'} <Kbd>⌘↵</Kbd>
          </Button>
          {style && (
            <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs">
              {`PAIRVOICE_STYLE=${style.name}\npairvoice say --style ${style.name} "…"`}
            </pre>
          )}
        </section>
      </div>
    </div>
  )
}
