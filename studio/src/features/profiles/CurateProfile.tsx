import { useQueryClient } from '@tanstack/react-query'
import { cn } from 'cn'
import { useState, useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ApiError, apiSend, apiSendForm, toErrorMessage } from '@/lib/api'
import type { CurateAnalysis, CurateReport, CurateResponse } from '@/lib/api-types'

import { CurateTimeline, formatClock } from './CurateTimeline'
import { Field } from './Field'
import { FilePicker } from './FilePicker'
import { invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// 長い・形式がばらばら・BGM の乗った音声や動画から、いちばん多く話す人のきれいな
// 区間を選んで参照音声にする。サーバーが解析して候補の区間と自動で選ぶ見込みを返し、
// タイムラインで聴いて選び直してから作る。解析はサーバーに残るので、選び直して何度でも作れる
export function CurateProfile({ onStatus, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [files, setFiles] = useState<File[]>([])
  const [name, setName] = useState('')
  const [caption, setCaption] = useState('')
  const [analysis, setAnalysis] = useState<CurateAnalysis | null>(null)
  // 作るときは画面で選んだ区間をそのまま送る（聴いて確かめた区間と、作る区間を食い違わせない）
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  // 作った声と、どの区間を使ったか。作ってすぐ声の画面へ移ると内訳が見えないので、ここに残す
  const [created, setCreated] = useState<CurateResponse | null>(null)
  const [analyzing, startAnalyzing] = useTransition()
  const [saving, startSaving] = useTransition()

  const seconds = analysis ? selectedSeconds(analysis, selected) : 0
  const limits = analysis?.limits
  const lengthOk = !!limits && seconds >= limits.min_seconds && seconds <= limits.max_seconds
  const canSave = !!analysis && name.trim() !== '' && lengthOk && !saving
  const isAuto = !!analysis && sameSet(selected, analysis.preselected)

  function forget() {
    if (analysis) {
      // 消さなくても1時間で消える。消せなくても画面の操作は止めない
      void apiSend(`/curate/${analysis.job_id}`, 'DELETE', undefined, {
        allowNotFound: true,
      }).catch(() => {})
    }
    setAnalysis(null)
    setCreated(null)
  }

  function analyze() {
    if (files.length === 0 || analyzing) return
    const form = new FormData()
    for (const file of files) form.append('files', file, file.name)
    forget()
    startAnalyzing(async () => {
      try {
        const result = await apiSendForm<CurateAnalysis>('/curate', form)
        setAnalysis(result)
        setSelected(new Set(result.preselected))
      } catch (err: unknown) {
        onStatus({ message: `解析できませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  function toggle(index: number) {
    setSelected((current) => {
      const next = new Set(current)
      if (!next.delete(index)) next.add(index)
      return next
    })
  }

  function save() {
    if (!canSave || !analysis) return
    const body = {
      name: name.trim(),
      caption: caption.trim(),
      pieces: [...selected].toSorted((a, b) => a - b),
    }
    startSaving(async () => {
      try {
        const response = await apiSend<CurateResponse>(
          `/curate/${analysis.job_id}/profile`,
          'POST',
          body,
          { allowNotFound: true },
        )
        await invalidateProfiles(queryClient)
        onStatus({ message: `「${response.profile.name}」を作りました`, isError: false })
        setCreated(response)
      } catch (err: unknown) {
        if (err instanceof ApiError && err.status === 404) {
          setAnalysis(null)
          onStatus({
            message: '解析が消えていました（1時間使わないと消えます）。もう一度解析してください',
            isError: true,
          })
          return
        }
        onStatus({ message: `作れませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  return (
    <div className="space-y-5">
      <div className="max-w-xl">
        <Field
          label="素材"
          hint="音声か動画のファイル。いくつでも、長くても、BGM が乗っていても構いません。いちばん多く話す人の声を区間に切り分け、雑音が少なくふだんの話し方に近い区間を選んだ状態で並べます。"
        >
          <div className="flex flex-wrap items-center gap-3">
            <FilePicker
              label="ファイルを選ぶ"
              accept="audio/*,video/*"
              multiple
              summary={describeFiles(files)}
              ariaLabel="素材のファイル"
              onPick={(picked) => {
                setFiles(picked)
                forget()
                if (picked[0] && name === '') setName(picked[0].name.replace(/\.[^.]+$/, ''))
              }}
            />
            <Button
              variant={analysis ? 'outline' : 'default'}
              disabled={files.length === 0 || analyzing}
              onClick={analyze}
            >
              {analysis ? '解析し直す' : '解析する'}
            </Button>
          </div>
        </Field>
        {analyzing && (
          <p className="mt-2 text-sm text-muted-foreground" role="status">
            解析しています。1時間の素材で数分かかります。
          </p>
        )}
      </div>

      {analysis && limits && (
        <>
          <section className="space-y-2" aria-label="区間を選ぶ">
            <AnalysisSummary analysis={analysis} />
            {/* 解析が替われば、倍率も鳴っている音も持ち越さない */}
            <CurateTimeline
              key={analysis.job_id}
              analysis={analysis}
              selected={selected}
              onToggle={toggle}
            />
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className={cn('tabular-nums', !lengthOk && 'text-destructive')} role="status">
                {selected.size} 区間・{seconds.toFixed(1)} 秒
                {seconds < limits.min_seconds && `（${limits.min_seconds} 秒以上選んでください）`}
                {seconds > limits.max_seconds && `（${limits.max_seconds} 秒までにしてください）`}
              </span>
              <span className="text-xs text-muted-foreground">
                {limits.target_seconds} 秒前後がおすすめ（{limits.max_seconds} 秒まで）
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={isAuto}
                onClick={() => setSelected(new Set(analysis.preselected))}
              >
                自動の選び方に戻す
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={selected.size === 0}
                onClick={() => setSelected(new Set())}
              >
                すべて外す
              </Button>
            </div>
          </section>

          <div className="max-w-xl space-y-5">
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
              {saving && (
                <span className="text-sm text-muted-foreground" role="status">
                  雑音を除いてつないでいます。
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
        </>
      )}
    </div>
  )
}

function sameSet(a: ReadonlySet<number>, b: number[]): boolean {
  return a.size === b.length && b.every((i) => a.has(i))
}

function selectedSeconds(analysis: CurateAnalysis, selected: ReadonlySet<number>): number {
  return analysis.candidates
    .filter((c) => selected.has(c.index))
    .reduce((sum, c) => sum + c.end - c.start, 0)
}

function describeFiles(files: File[]): string {
  const [first, ...rest] = files
  if (!first) return '選ばれていません'
  const megabytes = files.reduce((sum, f) => sum + f.size, 0) / 1024 ** 2
  const names = rest.length === 0 ? first.name : `${first.name} ほか ${rest.length} 件`
  return `${names}（${megabytes.toFixed(1)} MB）`
}

function AnalysisSummary({ analysis }: { analysis: CurateAnalysis }) {
  const talked = analysis.speakers.reduce((sum, s) => sum + s.seconds, 0)
  const chosen = analysis.speakers.find((s) => s.chosen)
  return (
    <div className="space-y-1 text-sm">
      <p>
        候補 {analysis.candidates.length}{' '}
        区間。雑音が少なく、ふだんの話し方に近い区間を選んであります。
      </p>
      {chosen && analysis.speakers.length > 1 && (
        <p className="text-muted-foreground">
          {analysis.speakers.length} 人の声のうち、いちばん多く話す人（{chosen.seconds} 秒・全体の{' '}
          {Math.round((chosen.seconds / talked) * 100)}%）の区間です。
        </p>
      )}
      {analysis.truncated && (
        <p className="text-muted-foreground">素材が長いので、3 時間より後ろは使っていません。</p>
      )}
    </div>
  )
}

// 作った参照音声が、どの人のどの区間から来たか
function CurateSummary({ report }: { report: CurateReport }) {
  return (
    <div className="space-y-2 text-sm">
      <p>
        {report.pieces.length} 区間（候補 {report.candidates} 区間）から、{report.seconds}{' '}
        秒の声を使いました。
      </p>
      <ul className="space-y-0.5 text-xs text-muted-foreground tabular-nums">
        {report.pieces.map((piece) => (
          <li key={`${piece.file}-${piece.start}`}>
            {piece.file} {formatClock(piece.start)}〜{formatClock(piece.end)}・S/N {piece.snr_db} dB
          </li>
        ))}
      </ul>
    </div>
  )
}
