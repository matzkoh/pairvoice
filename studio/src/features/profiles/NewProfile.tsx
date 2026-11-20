import type { ComponentProps } from 'react'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { ProfileItem } from '@/lib/api-types'

import { DesignProfile } from './DesignProfile'
import { FoldProfile } from './FoldProfile'
import { MixProfile } from './MixProfile'
import { PickProfile } from './PickProfile'
import { RandomProfile } from './RandomProfile'
import { UploadProfile } from './UploadProfile'

const METHODS = [
  { value: 'design', label: 'caption から声を作る', needs: '声の描写を文で書き、候補から選ぶ' },
  { value: 'pick', label: '2択で絞り込む', needs: '2つの声を聴き比べて、好みへ寄せていく' },
  { value: 'mix', label: '2つの声を混ぜる', needs: '手持ちの声2つの、間の声を選ぶ' },
  { value: 'upload', label: 'wav を取り込む', needs: '数秒〜十数秒の wav がある' },
  { value: 'curate', label: '素材から作る', needs: '長い音声か動画がある' },
  { value: 'random', label: 'ランダムに選ぶ', needs: '作った声を束ね、読み上げごとに替える' },
] as const

type Props = ComponentProps<typeof DesignProfile> & {
  voices: readonly ProfileItem[]
  foldJob: string | undefined
  onFoldJob: (job: string | undefined) => void
}

export function NewProfile({ voices, onStatus, onCreated, foldJob, onFoldJob, ...design }: Props) {
  return (
    <section aria-labelledby="new-profile-heading">
      <h2 id="new-profile-heading" className="text-xl leading-tight font-semibold tracking-tight">
        新しいプロファイルを作る
      </h2>
      <p className="mt-1.5 max-w-[52em] text-[13px] text-muted-foreground">
        参照音声の用意の仕方を選びます。最初の1つはそのまま使用中になります。ランダムは作った声から読み上げのたびに1つを選びます（Claude
        Code の読み上げでは、同じセッションの間は同じ声のままです）。
      </p>
      {/* 切り替えても作りかけの候補や入力を失わないよう、両方を載せたまま隠す */}
      {/* 畳み込みの仕事を追っている URL で来たら、「素材から作る」から始める */}
      <Tabs defaultValue={foldJob ? 'curate' : 'design'} className="mt-5 gap-6">
        {/* 作り方は、手元に何があるかで決まる。名前だけの並びでは選べないので、それを添える */}
        <TabsList className="grid w-full max-w-3xl grid-cols-3 gap-1.5 bg-transparent p-0 group-data-horizontal/tabs:h-auto max-md:grid-cols-2">
          {METHODS.map((method) => (
            <TabsTrigger
              key={method.value}
              value={method.value}
              className="h-auto flex-col items-start gap-0.5 rounded-xl border-transparent bg-background px-3 py-2 text-left whitespace-normal data-active:border-foreground data-active:bg-card dark:data-active:border-foreground dark:data-active:bg-card"
            >
              {method.label}
              <span className="text-xs font-normal text-muted-foreground">{method.needs}</span>
            </TabsTrigger>
          ))}
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
        <TabsContent value="mix" keepMounted>
          <MixProfile
            voices={voices}
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
          <FoldProfile
            jobId={foldJob}
            onJobChange={onFoldJob}
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
