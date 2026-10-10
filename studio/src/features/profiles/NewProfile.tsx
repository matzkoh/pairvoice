import type { ComponentProps } from 'react'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { DesignProfile } from './DesignProfile'
import { PickProfile } from './PickProfile'
import { UploadProfile } from './UploadProfile'

type Props = ComponentProps<typeof DesignProfile>

export function NewProfile({ onStatus, onCreated, ...design }: Props) {
  return (
    <section aria-labelledby="new-profile-heading" className="rounded-xl border bg-muted/40 p-5">
      <h2 id="new-profile-heading" className="text-base font-semibold">
        新しいプロファイルを作る
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        参照音声の用意の仕方を選びます。最初の1つはそのまま使用中になります。
      </p>
      {/* 切り替えても作りかけの候補や入力を失わないよう、両方を載せたまま隠す */}
      <Tabs defaultValue="design" className="mt-4 gap-5">
        <TabsList>
          <TabsTrigger value="design">caption から声を作る</TabsTrigger>
          <TabsTrigger value="pick">2択で絞り込む</TabsTrigger>
          <TabsTrigger value="upload">wav を取り込む</TabsTrigger>
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
      </Tabs>
    </section>
  )
}
