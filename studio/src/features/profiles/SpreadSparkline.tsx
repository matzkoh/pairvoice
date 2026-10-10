const W = 160
const H = 28
const PAD = 4

const percent = (v: number) => `${Math.round(v * 100)}%`

// 問いごとの候補の広がり（最初の問いを 1 とする）の小さな折れ線。1本だけなので凡例は置かず、
// 見出しで名前を示す
export function SpreadSparkline({ spreads }: { spreads: readonly number[] }) {
  const top = Math.max(...spreads, 1e-9)
  const x = (i: number) =>
    spreads.length === 1 ? W / 2 : PAD + (i / (spreads.length - 1)) * (W - 2 * PAD)
  const y = (v: number) => H - PAD - (v / top) * (H - 2 * PAD)
  const points = spreads.map((v, i) => `${x(i)},${y(v)}`).join(' ')
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-7 w-40"
      role="img"
      aria-label={`候補の広がり: ${spreads.map(percent).join('、')}`}
    >
      {/* 広がりが 0 の高さ。どこまで狭まったかが読める */}
      <line
        x1={PAD}
        x2={W - PAD}
        y1={H - PAD}
        y2={H - PAD}
        className="stroke-border"
        strokeWidth={1}
      />
      <polyline
        points={points}
        fill="none"
        className="stroke-primary"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      {spreads.map((v, i) => (
        <circle
          key={i}
          cx={x(i)}
          cy={y(v)}
          r={i === spreads.length - 1 ? 3 : 2}
          className="fill-primary"
        />
      ))}
    </svg>
  )
}
