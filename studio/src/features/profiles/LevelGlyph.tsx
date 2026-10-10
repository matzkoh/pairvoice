// 声の軸の段階を、言葉を読まなくても分かる小さな絵で示す（voiceAxes の AXES と対応）。
// 段階の並びは聴こえ方の連続なので、絵も段階の番号から連続に描く

// female は人型（年代）をワンピースで描くか。答えた・見立ての性別に合わせる
type Props = { axis: string; index: number; female?: boolean; className?: string }

const W = 48
const H = 32

export function LevelGlyph({ axis, index, female = false, className }: Props) {
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {GLYPHS[axis]?.(index, female)}
    </svg>
  )
}

// 正弦波の折れ線。cycles は幅いっぱいに入る波の数
function wave(cycles: number, amplitude: number, shape: (t: number) => number = Math.sin) {
  const points: string[] = []
  for (let x = 4; x <= W - 4; x += 1) {
    const t = ((x - 4) / (W - 8)) * cycles * 2 * Math.PI
    points.push(`${x},${(H / 2 - amplitude * shape(t)).toFixed(2)}`)
  }
  return `M${points.join(' L')}`
}

// 角を丸めた矩形波（はきはき）
const squarish = (t: number) => Math.tanh(4 * Math.sin(t))

// 案内表示の人型。dress はワンピースと長い髪、bent は前かがみで杖をつく（年配）。
// scale で背丈、wide で恰幅を変える（足もとをそろえたまま）
function Person({
  scale = 1,
  wide = 1,
  dress,
  bent,
}: {
  scale?: number
  wide?: number
  dress?: boolean
  bent?: boolean
}) {
  const cx = W / 2
  const foot = H - 1
  return (
    <g
      transform={`translate(${cx} ${foot}) scale(${scale * wide} ${scale}) rotate(${bent ? 10 : 0}) translate(${-cx} ${-foot})`}
      fill="currentColor"
      stroke="none"
    >
      <circle cx={cx} cy={5} r={4} />
      {dress ? (
        <>
          <path d={`M${cx - 4.5},5 q-1.5,5 -1,8 h11 q0.5,-3 -1,-8 z`} />
          <path d={`M${cx - 3},10 L${cx - 8},24 H${cx + 8} L${cx + 3},10 z`} />
          <rect x={cx - 4} y={24} width={2.5} height={7} rx={1} />
          <rect x={cx + 1.5} y={24} width={2.5} height={7} rx={1} />
        </>
      ) : (
        <>
          <rect x={cx - 5} y={10} width={10} height={12} rx={2} />
          <rect x={cx - 5} y={20} width={4.5} height={11} rx={1} />
          <rect x={cx + 0.5} y={20} width={4.5} height={11} rx={1} />
        </>
      )}
      {bent && <rect x={cx + 8} y={14} width={1.8} height={17} rx={0.9} />}
    </g>
  )
}

const GLYPHS: Record<string, (index: number, female: boolean) => React.ReactNode> = {
  gender: (index) => <Person dress={index === 1} />,
  // 背丈で年代を示す。四十代は恰幅よく、年配は少し前かがみで杖をつく
  age: (index, female) => (
    <Person
      scale={[0.7, 0.9, 1, 1, 0.95][index]}
      wide={index === 3 ? 1.25 : 1}
      dress={female}
      bent={index === 4}
    />
  ),
  // 低い声ほどゆったりした波、高い声ほど細かい波
  pitch: (index) => <path d={wave([1, 1.5, 2.5, 3.5, 5][index]!, 9)} />,
  texture: (index) => {
    switch (index) {
      case 0: // 息まじり: かすれた点線
        return <path d={wave(2, 8)} strokeDasharray="1 3" />
      case 1: // 丸い: 太くなめらか
        return <path d={wave(1.5, 8)} strokeWidth={4} opacity={0.8} />
      case 2: // 澄んだ: 細く整った波
        return <path d={wave(2.5, 8)} strokeWidth={1.25} />
      case 3: // はきはき: 角の立った波
        return <path d={wave(2.5, 8, squarish)} />
      default: // 力強い: 大きく太い波
        return <path d={wave(2, 13)} strokeWidth={3} />
    }
  },
  // 表情で雰囲気を示す。口の曲がりが段階とともに笑顔へ
  mood: (index) => {
    const cx = W / 2
    const curve = [0, 2, 4, 6, 7][index]!
    return (
      <g>
        <circle cx={cx} cy={H / 2} r={13} />
        {index === 1 ? (
          // 落ち着き: 目を閉じる
          <path d={`M${cx - 7},${H / 2 - 3} h4 M${cx + 3},${H / 2 - 3} h4`} />
        ) : (
          <g fill="currentColor" stroke="none">
            <circle cx={cx - 5} cy={H / 2 - 3} r={1.5} />
            <circle cx={cx + 5} cy={H / 2 - 3} r={1.5} />
          </g>
        )}
        <path
          d={`M${cx - 6},${H / 2 + 4} Q${cx},${H / 2 + 4 + curve} ${cx + 6},${H / 2 + 4}`}
          fill={index === 4 ? 'currentColor' : 'none'}
        />
      </g>
    )
  },
  // スピードメーターの針
  speed: (index) => {
    const cx = W / 2
    const cy = H - 5
    const r = 18
    const angle = Math.PI - (Math.PI * (index + 0.5)) / 5
    return (
      <g>
        <path d={`M${cx - r},${cy} A${r},${r} 0 0 1 ${cx + r},${cy}`} />
        <path
          d={`M${cx},${cy} L${(cx + (r - 4) * Math.cos(angle)).toFixed(2)},${(cy - (r - 4) * Math.sin(angle)).toFixed(2)}`}
          strokeWidth={2.5}
        />
        <circle cx={cx} cy={cy} r={2} fill="currentColor" />
      </g>
    )
  },
}
