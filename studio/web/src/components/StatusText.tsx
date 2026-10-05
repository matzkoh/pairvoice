import { cn } from 'cn'

type Props = {
  message: string
  isError: boolean
  // 置き場所の要素に合わせる（ボタンと並ぶ行の中なら span、段落なら p）
  as?: 'p' | 'span' | 'div'
  className?: string
}

// 操作の結果を1行で知らせる。失敗だけ色を変える
export function StatusText({ message, isError, as: Tag = 'p', className }: Props) {
  return (
    <Tag
      className={cn('text-xs', isError ? 'text-destructive' : 'text-muted-foreground', className)}
    >
      {message}
    </Tag>
  )
}
