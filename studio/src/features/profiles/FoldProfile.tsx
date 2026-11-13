import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useEffectEvent, useState, useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ApiError, apiGet, apiSendForm, toErrorMessage } from '@/lib/api'
import type { FoldJob, FoldStage } from '@/lib/api-types'

import { Field } from './Field'
import { FilePicker } from './FilePicker'
import { ProgressBar } from './ProgressBar'
import { invalidateProfiles, type ProfileStatus } from './queries'

// 走っている仕事を問い合わせる間隔
export const POLL_MS = 3000

const STAGE_LABELS: Record<FoldStage, string> = {
  upload: '素材を受け取りました',
  decode: '素材を読み込んでいます',
  clean: '雑音と響きを除いています',
  diarize: '話者を分けています',
  encode: '声を話者の表現にしています',
  summarize: '表現を要約しています',
  save: '声を保存しています',
}

type Props = {
  // 追っている仕事の ID（URL の ?fold=）。再読み込みしても進み具合を追い直せる
  jobId?: string
  onJobChange?: (jobId: string | undefined) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// 長い素材の本人の声を全部、話者の表現にして1つに畳み込む。区間を選ばないので、
// 素材全体の平均の声になる。数十分かかるので、サーバーが後ろで走らせ、ここは進み具合を追う
export function FoldProfile({ jobId, onJobChange, onStatus, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [files, setFiles] = useState<File[]>([])
  const [name, setName] = useState('')
  const [caption, setCaption] = useState('')
  const [job, setJob] = useState<FoldJob | null>(null)
  const [sending, startSending] = useTransition()
  const running = job?.status === 'running' && job.job_id === jobId
  const canStart = files.length > 0 && name.trim() !== '' && !sending && !running

  const finished = useEffectEvent(async (result: FoldJob) => {
    if (result.status === 'done' && result.profile) {
      await invalidateProfiles(queryClient)
      onStatus({ message: `「${result.profile.name}」を作りました`, isError: false })
    } else if (result.status === 'failed') {
      onStatus({ message: `作れませんでした: ${failureMessage(result)}`, isError: true })
    }
  })
  const lost = useEffectEvent(() => {
    setJob(null)
    onJobChange?.(undefined)
    onStatus({
      message: '仕事が見つかりません（常駐サーバーを再起動すると消えます）',
      isError: true,
    })
  })

  // 仕事が終わるまで問い合わせる
  useEffect(() => {
    const polling = { current: true, timer: undefined as ReturnType<typeof setTimeout> | undefined }
    const again = () => {
      polling.timer = setTimeout(() => void poll(), POLL_MS)
    }
    const poll = async () => {
      if (!jobId) return
      try {
        const result = await apiGet<FoldJob>(`/profiles/fold/${jobId}`, { allowNotFound: true })
        if (!polling.current) return
        setJob(result)
        if (result.status === 'running') again()
        else await finished(result)
      } catch (err: unknown) {
        if (!polling.current) return
        if (err instanceof ApiError && err.status === 404) lost()
        // 一時的に届かないだけかもしれないので、問い合わせは続ける
        else again()
      }
    }
    void poll()
    return () => {
      polling.current = false
      clearTimeout(polling.timer)
    }
  }, [jobId])

  function start() {
    if (!canStart) return
    const form = new FormData()
    for (const file of files) form.append('files', file, file.name)
    const query = new URLSearchParams({ name: name.trim(), caption: caption.trim() })
    startSending(async () => {
      try {
        const started = await apiSendForm<FoldJob>(`/profiles/fold?${query}`, form)
        setJob(started)
        onJobChange?.(started.job_id)
      } catch (err: unknown) {
        onStatus({ message: `送れませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  return (
    <div className="max-w-xl space-y-5">
      <Field
        label="素材"
        hint="音声か動画のファイル。いくつでも、長くても、BGM が乗っていても構いません。雑音と響きを除いてから、いちばん多く話す人の声を全部使います。長いほど声がぶれません（数十分〜数時間が目安）。"
      >
        <FilePicker
          label="ファイルを選ぶ"
          accept="audio/*,video/*"
          multiple
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
        htmlFor="fold-caption"
        hint="話し方の指示。合成のたびに声と併せて渡ります。空なら声だけで読みます。"
      >
        <Textarea
          id="fold-caption"
          rows={2}
          placeholder="例: 落ち着いた低い声。ゆっくりと丁寧に話す。"
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
        />
      </Field>
      <Field label="名前" htmlFor="fold-name">
        <Input id="fold-name" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div className="space-y-2">
        <Button disabled={!canStart} onClick={start}>
          プロファイルを作る
        </Button>
        {sending && (
          <p className="text-sm text-muted-foreground" role="status">
            素材を送っています。
          </p>
        )}
        {job && <JobStatus job={job} onOpen={onCreated} />}
      </div>
    </div>
  )
}

function JobStatus({ job, onOpen }: { job: FoldJob; onOpen: (id: string) => void }) {
  if (job.status === 'running') {
    return (
      <div className="space-y-1" role="status">
        <div className="flex items-center gap-3 text-sm">
          <span>{STAGE_LABELS[job.stage]}</span>
          <ProgressBar value={job.progress} />
        </div>
        <p className="text-xs text-muted-foreground">
          素材 30 分につき 20 分ほどかかります。この画面を離れても、サーバーで続きます。
        </p>
      </div>
    )
  }
  if (job.status === 'failed' || !job.profile || !job.report) {
    return (
      <p className="text-sm text-destructive" role="status">
        作れませんでした: {failureMessage(job)}
      </p>
    )
  }
  const { profile, report } = job
  return (
    <div className="space-y-3 rounded-lg border bg-background p-4">
      <p className="text-sm">
        本人の声 {Math.round(report.speech_seconds / 60)} 分（{report.pieces} 区間）を、
        {report.tokens} トークンに畳み込みました。
      </p>
      <Button variant="outline" size="sm" onClick={() => onOpen(profile.id)}>
        「{profile.name}」を開く
      </Button>
    </div>
  )
}

function failureMessage(job: FoldJob): string {
  return job.detail ?? job.error ?? '不明なエラー'
}

function describeFiles(files: File[]): string {
  const [first, ...rest] = files
  if (!first) return '選ばれていません'
  const megabytes = files.reduce((sum, f) => sum + f.size, 0) / 1024 ** 2
  const names = rest.length === 0 ? first.name : `${first.name} ほか ${rest.length} 件`
  return `${names}（${megabytes.toFixed(1)} MB）`
}
