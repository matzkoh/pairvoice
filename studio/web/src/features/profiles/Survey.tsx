import { Button } from '@/components/ui/button'

import { LevelGlyph } from './LevelGlyph'
import { AXES } from './voiceAxes'

type SurveyProps = {
  at: number
  female: boolean
  onAnswer: (level: number | undefined) => void
  onBack: () => void
  onSkipRest: () => void
}

// 1画面に1軸ずつ聞く。決まっていなければ「わからない」で2択に回す
export function Survey({ at, female, onAnswer, onBack, onSkipRest }: SurveyProps) {
  const axis = AXES[at]!
  return (
    <div className="max-w-2xl space-y-5">
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground">
          事前の質問 {at + 1} / {AXES.length}
        </p>
        <h3 className="text-base font-medium">{axis.label}は決まっていますか？</h3>
      </div>
      <div className="flex flex-wrap gap-2">
        {axis.levels.map((level, index) => (
          <Button
            key={level.label}
            variant="outline"
            className="h-auto w-24 flex-col gap-1 py-2"
            onClick={() => onAnswer(index)}
          >
            <LevelGlyph axis={axis.key} index={index} female={female} className="size-12" />
            {level.label}
          </Button>
        ))}
        <Button
          variant="secondary"
          className="h-auto w-24 flex-col gap-1 py-2"
          onClick={() => onAnswer(undefined)}
        >
          <span aria-hidden="true" className="flex size-12 items-center justify-center text-2xl">
            ?
          </span>
          わからない
        </Button>
      </div>
      <div className="flex gap-2">
        <Button variant="ghost" size="sm" onClick={onBack}>
          戻る
        </Button>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={onSkipRest}>
          残りは聞き比べで決める
        </Button>
      </div>
    </div>
  )
}
