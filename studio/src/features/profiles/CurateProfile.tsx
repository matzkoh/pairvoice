import { useQueryClient } from '@tanstack/react-query'
import { useState, useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { apiSendForm, toErrorMessage } from '@/lib/api'
import type { CurateReport, CurateResponse } from '@/lib/api-types'

import { Field } from './Field'
import { FilePicker } from './FilePicker'
import { invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// 長い・形式がばらばら・BGM の乗った音声や動画から、いちばん多く話す人のきれいな
// 区間を選んで参照音声にする。選り分けはサーバーが行い、どの区間を使ったかを返す
export function CurateProfile({ onStatus, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [files, setFiles] = useState<File[]>([])
  const [name, setName] = useState('')
  const [caption, setCaption] = useState('')
  // 作った声と、どの区間を使ったか。作ってすぐ声の画面へ移ると内訳が見えないので、ここに残す
  const [created, setCreated] = useState<CurateResponse | null>(null)
  const [pending, startTransition] = useTransition()
  // 保存後に選択を空にするには input を作り直すしかない（value を書き換えられない）
  const [inputKey, setInputKey] = useState(0)
  const canSave = files.length > 0 && name.trim() !== '' && !pending

  function save() {
    if (!canSave) return
    const query = new URLSearchParams({ name: name.trim(), caption: caption.trim() })
    const form = new FormData()
    for (const file of files) form.append('files', file, file.name)
    startTransition(async () => {
      try {
        const response = await apiSendForm<CurateResponse>(`/profiles/curate?${query}`, form)
        await invalidateProfiles(queryClient)
        onStatus({ message: `「${response.profile.name}」を作りました`, isError: false })
        setCreated(response)
        setFiles([])
        setName('')
        setCaption('')
        setInputKey((k) => k + 1)
      } catch (err: unknown) {
        onStatus({ message: `作れませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  return (
    <div className="max-w-xl space-y-5">
      <Field
        label="素材"
        hint="音声か動画のファイル。いくつでも、長くても、BGM が乗っていても構いません。いちばん多く話す人の声から、雑音の少ない区間を合わせて 20 秒ほど選び、雑音を除いてつなぎます。"
      >
        <FilePicker
          label="ファイルを選ぶ"
          accept="audio/*,video/*"
          multiple
          resetKey={inputKey}
          summary={describeFiles(files)}
          ariaLabel="素材のファイル"
          onPick={(picked) => {
            setFiles(picked)
            if (picked[0] && name === '') setName(picked[0].name.replace(/\.[^.]+$/, ''))
          }}
        />
      </Field>
      <Field
        label="caption"
        htmlFor="curate-caption"
        hint="話し方の指示。合成のたびに参照音声と併せて渡ります。空なら参照音声だけで読みます。"
      >
        <Textarea
          id="curate-caption"
          rows={2}
          placeholder="例: 落ち着いた低い声。ゆっくりと丁寧に話す。"
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
        />
      </Field>
      <Field label="名前" htmlFor="curate-name">
        <Input id="curate-name" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div className="flex items-center gap-3">
        <Button disabled={!canSave} onClick={save}>
          プロファイルを作る
        </Button>
        {pending && (
          <span className="text-sm text-muted-foreground" role="status">
            選り分けています。1時間の素材で数分かかります。
          </span>
        )}
      </div>
      {created && (
        <div className="space-y-3 rounded-lg border bg-background p-4">
          <CurateSummary report={created.report} />
          <Button variant="outline" size="sm" onClick={() => onCreated(created.profile.id)}>
            「{created.profile.name}」を開く
          </Button>
        </div>
      )}
    </div>
  )
}

function describeFiles(files: File[]): string {
  const [first, ...rest] = files
  if (!first) return '選ばれていません'
  const megabytes = files.reduce((sum, f) => sum + f.size, 0) / 1024 ** 2
  const names = rest.length === 0 ? first.name : `${first.name} ほか ${rest.length} 件`
  return `${names}（${megabytes.toFixed(1)} MB）`
}

// 作った参照音声が、どの人のどの区間から来たか。聞いて違和感があれば素材を足して作り直す
function CurateSummary({ report }: { report: CurateReport }) {
  const talked = report.speakers.reduce((sum, s) => sum + s.seconds, 0)
  const chosen = report.speakers.find((s) => s.chosen)
  return (
    <div className="space-y-2 text-sm">
      <p>
        {report.pieces.length} 区間（候補 {report.candidates} 区間）から、{report.seconds}{' '}
        秒の声を使いました。
      </p>
      {chosen && report.speakers.length > 1 && (
        <p className="text-muted-foreground">
          {report.speakers.length} 人の声のうち、いちばん多く話す人（{chosen.seconds} 秒・全体の{' '}
          {Math.round((chosen.seconds / talked) * 100)}%）を選びました。
        </p>
      )}
      {report.truncated && (
        <p className="text-muted-foreground">素材が長いので、3 時間より後ろは使っていません。</p>
      )}
      <ul className="space-y-0.5 text-xs text-muted-foreground tabular-nums">
        {report.pieces.map((piece) => (
          <li key={`${piece.file}-${piece.start}`}>
            {piece.file} {formatTime(piece.start)}〜{formatTime(piece.end)}・S/N {piece.snr_db} dB
          </li>
        ))}
      </ul>
    </div>
  )
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}
