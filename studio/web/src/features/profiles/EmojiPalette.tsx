import { EMOJI_ANNOTATIONS } from './emojiAnnotations'

type Props = { onInsert: (emoji: string) => void }

export function EmojiPalette({ onInsert }: Props) {
  return (
    <div className="flex flex-wrap gap-1">
      {EMOJI_ANNOTATIONS.map((item) => (
        <button
          key={item.emoji}
          type="button"
          title={item.en}
          // 押した瞬間にテキスト欄のフォーカス（＝挿入位置）を奪わない
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onInsert(item.emoji)}
          className="flex items-center gap-1 rounded-md border bg-muted/50 px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <span className="text-[15px] leading-none">{item.emoji}</span>
          <span className="max-w-[8.5em] truncate">{item.ja}</span>
        </button>
      ))}
    </div>
  )
}
