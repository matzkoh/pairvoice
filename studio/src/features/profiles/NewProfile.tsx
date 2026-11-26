import type { ComponentProps } from 'react'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { ProfileItem } from '@/lib/api-types'

import { DesignProfile } from './DesignProfile'
import { FoldProfile } from './FoldProfile'
import { HOLDINGS, holdingText } from './holding'
import { MixProfile } from './MixProfile'
import { PickProfile } from './PickProfile'
import { RandomProfile } from './RandomProfile'
import { UploadProfile } from './UploadProfile'

// 作り方は、手元に何があるかで決まる。名前だけの並びでは選べないので、それを添える。
// 作り方で声の持ち方（holding.ts）が決まり、そこで良し悪しが分かれるので、持ち方ごとに並べる
const GROUPS = [
  {
    holding: 'speaker',
    methods: [
      { value: 'pick', label: '2択で絞り込む', needs: '2つの声を聴き比べて、好みへ寄せていく' },
      { value: 'mix', label: '2つの声を混ぜる', needs: '手持ちの声2つの、間の声を選ぶ' },
      { value: 'curate', label: '素材から作る', needs: '長い音声か動画がある' },
    ],
  },
  {
    holding: 'reference',
    methods: [
      { value: 'design', label: 'caption から声を作る', needs: '声の描写を文で書き、候補から選ぶ' },
      { value: 'upload', label: 'wav を取り込む', needs: '数秒〜十数秒の wav がある' },
    ],
  },
  {
    holding: 'random',
    methods: [
      { value: 'random', label: 'ランダムに選ぶ', needs: '作った声を束ね、読み上げごとに替える' },
    ],
  },
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
        手元にあるものから作り方を選びます。作り方で声の持ち方が決まり、合成モデルを替えたときの扱いが変わります。最初の1つはそのまま使用中になります。
      </p>
      {/* 切り替えても作りかけの候補や入力を失わないよう、両方を載せたまま隠す */}
      {/* 畳み込みの仕事を追っている URL で来たら、「素材から作る」から始める */}
      <Tabs defaultValue={foldJob ? 'curate' : 'design'} className="mt-5 gap-6">
        <TabsList className="grid w-full max-w-3xl gap-4 bg-transparent p-0 group-data-horizontal/tabs:h-auto">
          {GROUPS.map((group) => {
            return (
              <div key={group.holding} className="space-y-1.5">
                <p className="px-1 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">
                    {HOLDINGS[group.holding].label}
                  </span>
                  <span className="ml-2">{holdingText(group.holding)}</span>
                </p>
                <div className="grid grid-cols-3 gap-1.5 max-md:grid-cols-2">
                  {group.methods.map((method) => (
                    <TabsTrigger
                      key={method.value}
                      value={method.value}
                      className="h-auto flex-col items-start gap-0.5 rounded-xl border-transparent bg-background px-3 py-2 text-left whitespace-normal data-active:border-foreground data-active:bg-card dark:data-active:border-foreground dark:data-active:bg-card"
                    >
                      {method.label}
                      <span className="text-xs font-normal text-muted-foreground">
                        {method.needs}
                      </span>
                    </TabsTrigger>
                  ))}
                </div>
              </div>
            )
          })}
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
