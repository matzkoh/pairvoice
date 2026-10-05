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

import { captionHistorySource, invalidateProfiles } from './queries'

// 履歴はプロファイルごとに持つ
export function CaptionHistorySheet({ profileId }: { profileId: string }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState('')
  return (
    <Sheet>
      <SheetTrigger render={<Button variant="outline" size="sm" />}>
        <History className="size-3.5" aria-hidden="true" />
        caption の履歴
      </SheetTrigger>
      <SheetContent side="right" className="w-96">
        <SheetHeader>
          <SheetTitle>caption の履歴</SheetTitle>
          <SheetDescription>
            このプロファイルで採用した caption の版です。復元すると次の読み上げから効きます。
          </SheetDescription>
        </SheetHeader>
        <div className="px-4">
          {error && <p className="mb-2 text-xs text-destructive">復元に失敗しました: {error}</p>}
          <Suspense fallback={<p className="text-sm text-muted-foreground">読み込み中…</p>}>
            <HistoryList
              source={captionHistorySource(profileId)}
              onRestored={() => invalidateProfiles(queryClient)}
              onError={setError}
            />
          </Suspense>
        </div>
      </SheetContent>
    </Sheet>
  )
}
