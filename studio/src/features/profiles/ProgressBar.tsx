// 進み具合の細い棒。value は 0〜1
export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-primary transition-[width]"
        style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }}
      />
    </div>
  )
}
