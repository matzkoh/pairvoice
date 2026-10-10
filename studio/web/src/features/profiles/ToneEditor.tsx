import { useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { apiSend, toErrorMessage } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'
import { useDraft } from '@/lib/useDraft'

import { ProfileHistorySheet } from './ProfileHistorySheet'
import { invalidateProfiles, type ProfileStatus } from './queries'

// 声ごとの要約の口調。キャラクターの声なら、口調も声と一緒に決める
export function ToneEditor({
  item,
  onStatus,
}: {
  item: ProfileItem
  onStatus: (status: ProfileStatus) => void
}) {
  const queryClient = useQueryClient()
  const [pending, startTransition] = useTransition()
  const { draft, setDraft, markSubmitted } = useDraft(item.tone)
  const dirty = draft !== item.tone

  function save() {
    markSubmitted(draft)
    startTransition(async () => {
      try {
        await apiSend(`/profiles/${encodeURIComponent(item.id)}`, 'PATCH', { tone: draft })
        await invalidateProfiles(queryClient)
        onStatus({ message: '口調を保存しました（次の読み上げから効きます）', isError: false })
      } catch (err: unknown) {
        markSubmitted(null)
        onStatus({ message: `口調を保存できませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  return (
    <section aria-label="要約の口調" className="rounded-lg border bg-card p-4">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold">要約の口調</h2>
        <span className="text-xs text-muted-foreground">
          {dirty ? '未保存の変更あり' : item.tone.trim() ? '保存済み' : '既定の口調で読む'}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <ProfileHistorySheet profileId={item.id} field="tone" />
          <Button variant="ghost" size="sm" disabled={!dirty} onClick={() => setDraft(item.tone)}>
            変更を破棄
          </Button>
          <Button size="sm" disabled={!dirty || pending} onClick={save}>
            {pending ? '保存中…' : '保存'}
          </Button>
        </div>
      </div>
      <Textarea
        aria-label="要約の口調"
        rows={8}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        className="font-mono text-[13px] leading-relaxed"
      />
      <p className="mt-1 text-xs text-muted-foreground">
        この声で読むときの、口調の指示と入出力の例です。要約のプロンプトの共通の部分の後ろに足します。空にすると
        <Link to="/prompt" search={{ target: 'tone' }} className="underline">
          既定の口調
        </Link>
        で読みます。
      </p>
    </section>
  )
}
