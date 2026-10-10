import { useQueryClient, useSuspenseQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useActionState, useState } from 'react'

import { PageHeader, SectionHeading } from '@/components/PageHeader'
import { StatusText } from '@/components/StatusText'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { HistoryList } from '@/features/history/HistoryList'
import { apiSend, toErrorMessage } from '@/lib/api'
import { useDraft } from '@/lib/useDraft'
import { useTransientStatus } from '@/lib/useTransientStatus'

import { type PromptTarget, parseTarget, TARGET_SOURCES, textQueryOptions } from './queries'

type SaveState = { message: string; isError: boolean }

export function PromptEditor() {
  const { target: param } = useSearch({ from: '/prompt' })
  const navigate = useNavigate({ from: '/prompt' })
  const target = parseTarget(param)

  const tabs = (
    <Tabs
      value={target}
      onValueChange={(value) =>
        void navigate({ search: { target: value === 'common' ? undefined : String(value) } })
      }
      className="mb-4"
    >
      <TabsList>
        <TabsTrigger value="common">共通</TabsTrigger>
        <TabsTrigger value="tone">既定の口調</TabsTrigger>
      </TabsList>
    </Tabs>
  )
  // 対象を変えたら下書きと保存の表示を作り直す
  return <TextEditor key={target} target={target} tabs={tabs} />
}

const DESCRIPTIONS: Record<PromptTarget, { title: string; label: string; description: string }> = {
  common: {
    title: 'プロンプト',
    label: 'プロンプト',
    description:
      '要約の削り方と言葉選びなど、どの声でも共通の部分です。読み上げのたびに、声の口調を後ろに足して使います。',
  },
  tone: {
    title: '既定の口調',
    label: '口調',
    description:
      '口調を持たない声が使う、口調の指示と入出力の例です。声ごとの口調はプロファイル画面で書きます。',
  },
}

function TextEditor({ target, tabs }: { target: PromptTarget; tabs: React.ReactNode }) {
  const source = TARGET_SOURCES[target]
  const { data } = useSuspenseQuery(textQueryOptions(source))
  const queryClient = useQueryClient()
  const { title, label, description } = DESCRIPTIONS[target]

  // 保存・復元でサーバー側の本文が変わったら編集中の値も追随させる。key で作り直す
  // 方式だと、保存直後に出る「保存しました」が一緒に消えてしまうので選ばない。
  const { draft: text, setDraft: setText, markSubmitted } = useDraft(data.text)
  const dirty = text !== data.text

  const [restoreError, setRestoreError] = useState('')

  const [state, submit, pending] = useActionState<SaveState, FormData>(
    async (_prev, formData) => {
      setRestoreError('')
      const raw = formData.get('text')
      const value = typeof raw === 'string' ? raw : ''
      try {
        await apiSend(source.path, 'PUT', { text: value })
        // 履歴は本体のキーの下にあるので、まとめて取り直される
        await queryClient.invalidateQueries({ queryKey: source.key })
        return { message: '保存しました', isError: false }
      } catch (err: unknown) {
        markSubmitted(null)
        const message = toErrorMessage(err)
        return { message: `保存に失敗しました: ${message}`, isError: true }
      }
    },
    { message: '', isError: false },
  )

  // useActionState の state 自体は書き換えられない。保存成功のメッセージだけ自動で
  // 消したいので、「いま表示すべき値」を返す useTransientStatus に委ねる。
  const displayState = useTransientStatus(state.message ? state : null)

  // 復元の失敗も保存の状態表示に出す。
  const status = restoreError
    ? { message: `復元に失敗しました: ${restoreError}`, isError: true }
    : displayState

  return (
    <div>
      <PageHeader
        title={title}
        description={`${description}保存すると旧版は履歴に残ります。`}
        actions={
          <>
            {status && <StatusText as="span" message={status.message} isError={status.isError} />}
            <Button variant="ghost" size="sm" onClick={() => setText(data.text)}>
              変更を破棄
            </Button>
            <Button size="sm" type="submit" form="prompt-form" disabled={pending}>
              {pending ? '保存中…' : '保存（旧版は履歴へ）'}
            </Button>
          </>
        }
      />
      {tabs}
      <div className="grid grid-cols-[minmax(0,1fr)_16rem] items-start gap-6">
        {/* 送った値は送信の時点で覚える。action の中で覚えると transition の更新になり、
            保存の後に取り直した本文が先に描かれて、打ち足した分を上書きすることがある */}
        <form id="prompt-form" action={submit} onSubmit={() => markSubmitted(text)}>
          <Textarea
            name="text"
            aria-label={label}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="min-h-[60vh] resize-y font-mono text-[13px] leading-relaxed"
          />
          <p className="mt-2 text-xs text-muted-foreground tabular-nums">
            {text.length.toLocaleString('ja-JP')} 文字{dirty && '・未保存の変更あり'}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            レビューをもとに改善するときは、Claude Code で{' '}
            <span className="font-mono text-foreground">/pairvoice:tune</span> を実行します。
          </p>
        </form>
        <aside>
          <SectionHeading>履歴</SectionHeading>
          <HistoryList source={source} confirmBeforeRestore={dirty} onError={setRestoreError} />
        </aside>
      </div>
    </div>
  )
}
