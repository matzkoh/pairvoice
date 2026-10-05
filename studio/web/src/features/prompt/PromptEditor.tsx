import { useQueryClient, useSuspenseQuery } from '@tanstack/react-query'
import { useActionState, useState } from 'react'

import { PageHeader, SectionHeading } from '@/components/PageHeader'
import { StatusText } from '@/components/StatusText'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { HistoryList } from '@/features/history/HistoryList'
import { PROMPT_HISTORY } from '@/features/history/queries'
import { apiSend, toErrorMessage } from '@/lib/api'
import { useDraft } from '@/lib/useDraft'
import { useTransientStatus } from '@/lib/useTransientStatus'

import { promptQueryOptions } from './queries'

type SaveState = { message: string; isError: boolean }

export function PromptEditor() {
  const { data } = useSuspenseQuery(promptQueryOptions())
  const queryClient = useQueryClient()

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
      markSubmitted(value)
      try {
        await apiSend('/api/prompt', 'PUT', { text: value })
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['prompt'] }),
          queryClient.invalidateQueries({ queryKey: ['prompt', 'history'] }),
        ])
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
        title="プロンプト"
        description="読み上げ用の要約を作るプロンプトです。保存すると旧版は履歴に残ります。"
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
      <div className="grid grid-cols-[minmax(0,1fr)_16rem] items-start gap-6">
        <form id="prompt-form" action={submit}>
          <Textarea
            name="text"
            aria-label="プロンプト"
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
          <HistoryList
            source={PROMPT_HISTORY}
            confirmBeforeRestore={dirty}
            onError={setRestoreError}
          />
        </aside>
      </div>
    </div>
  )
}
