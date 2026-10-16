import type { ComponentProps } from 'react'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { ProfileItem } from '@/lib/api-types'

import { CurateProfile } from './CurateProfile'
import { DesignProfile } from './DesignProfile'
import { PickProfile } from './PickProfile'
import { RandomProfile } from './RandomProfile'
import { UploadProfile } from './UploadProfile'

type Props = ComponentProps<typeof DesignProfile> & {
  voices: readonly ProfileItem[]
  curateJob: string | undefined
  onCurateJob: (job: string | undefined) => void
}

export function NewProfile({
  voices,
  onStatus,
  onCreated,
  curateJob,
  onCurateJob,
  ...design
}: Props) {
  return (
    <section aria-labelledby="new-profile-heading" className="rounded-xl border bg-muted/40 p-5">
      <h2 id="new-profile-heading" className="text-base font-semibold">
        新しいプロファイルを作る
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        参照音声の用意の仕方を選びます。最初の1つはそのまま使用中になります。ランダムは作った声から読み上げのたびに1つを選びます。
      </p>
      {/* 切り替えても作りかけの候補や入力を失わないよう、両方を載せたまま隠す */}
      {/* 解析の URL で来たら、その解析を開いた「素材から作る」から始める */}
      <Tabs defaultValue={curateJob ? 'curate' : 'design'} className="mt-4 gap-5">
        <TabsList>
          <TabsTrigger value="design">caption から声を作る</TabsTrigger>
          <TabsTrigger value="pick">2択で絞り込む</TabsTrigger>
          <TabsTrigger value="upload">wav を取り込む</TabsTrigger>
          <TabsTrigger value="curate">素材から作る</TabsTrigger>
          <TabsTrigger value="random">ランダムに選ぶ</TabsTrigger>
        </TabsList>
        <TabsContent value="design" keepMounted>
          <DesignProfile {...design} onStatus={onStatus} onCreated={onCreated} />
        </TabsContent>
        <TabsContent value="pick" keepMounted>
          <PickProfile
            anchorText={design.anchorText}
            pairvoiceDown={design.pairvoiceDown}
            onPlay={design.onPlay}
            onStatus={onStatus}
            onCreated={onCreated}
          />
        </TabsContent>
        <TabsContent value="upload" keepMounted>
          <UploadProfile onStatus={onStatus} onCreated={onCreated} />
        </TabsContent>
        <TabsContent value="curate" keepMounted>
          <CurateProfile
            jobId={curateJob}
            onJobChange={onCurateJob}
            onStatus={onStatus}
            onCreated={onCreated}
          />
        </TabsContent>
        <TabsContent value="random" keepMounted>
          <RandomProfile voices={voices} onStatus={onStatus} onCreated={onCreated} />
        </TabsContent>
      </Tabs>
    </section>
  )
}
