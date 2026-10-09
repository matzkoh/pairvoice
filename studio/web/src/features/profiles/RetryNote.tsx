import { Button } from '@/components/ui/button'

type Props = { message: string; onRetry: () => void }

// 失敗を知らせる1行に、やり直しのボタンを添える
export function RetryNote({ message, onRetry }: Props) {
  return (
    <p className="text-xs text-destructive">
      {message}{' '}
      <Button variant="link" size="sm" className="h-auto p-0" onClick={onRetry}>
        もう一度
      </Button>
    </p>
  )
}
