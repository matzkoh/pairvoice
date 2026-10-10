import { useQueryClient } from '@tanstack/react-query'
import { History } from 'lucide-react'
import { Suspense, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { HistoryList } from '@/features/history/HistoryList'

import { invalidateProfiles, profileHistorySource } from './queries'

const LABELS = {
  caption: {
    title: 'caption の履歴',
    description:
      'このプロファイルで保存した caption の版です。復元すると次の読み上げから効きます。',
  },
  tone: {
    title: '口調の履歴',
    description:
      'このプロファイルで保存した要約の口調の版です。復元すると次の読み上げから効きます。',
  },
}

// プロファイルごとに版を持つもの（caption と、要約の口調）の履歴
export function ProfileHistorySheet({
  profileId,
  field,
}: {
  profileId: string
  field: 'caption' | 'tone'
}) {
  const label = LABELS[field]
  const queryClient = useQueryClient()
  const [error, setError] = useState('')
  return (
    <Sheet>
      <SheetTrigger render={<Button variant="outline" size="sm" />}>
        <History className="size-3.5" aria-hidden="true" />
        {label.title}
      </SheetTrigger>
      <SheetContent side="right" className="w-96">
        <SheetHeader>
          <SheetTitle>{label.title}</SheetTitle>
          <SheetDescription>{label.description}</SheetDescription>
        </SheetHeader>
        <div className="px-4">
          {error && <p className="mb-2 text-xs text-destructive">復元に失敗しました: {error}</p>}
          <Suspense fallback={<p className="text-sm text-muted-foreground">読み込み中…</p>}>
            <HistoryList
              source={profileHistorySource(profileId, field)}
              onRestored={() => invalidateProfiles(queryClient)}
              onError={setError}
            />
          </Suspense>
        </div>
      </SheetContent>
    </Sheet>
  )
}
