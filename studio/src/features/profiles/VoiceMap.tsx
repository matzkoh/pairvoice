import { motion } from 'motion/react'
import { useMemo } from 'react'

import type { MapData, MapPoint, Side } from './voiceRegion'

type Props = { map: MapData; playing: Side | null }

const W = 360
const H = 220
const PAD = 22

const SIDE_COLOR = { a: 'var(--pick-a)', b: 'var(--pick-b)' } as const
// 問いが変わったときの動き。印と線はばねで移す
const SPRING = { type: 'spring', stiffness: 140, damping: 22 } as const

// 声の空間の地図。外挿まで含めた全体と、手持ちの声が囲むアンケートの範囲、いまの候補の範囲
// （斜線）、見立てとその道のり、A と B と2つを分ける切れ目を描く。見立てを原点に、横軸を B から
// A への向きにするので、切れ目は縦の線になり、答えで範囲のどちら側が残るかが見たとおりになる
export function VoiceMap({ map, playing }: Props) {
  const shape = useMemo(() => {
    const { sx, sy } = scales(map)
    const at = (p: MapPoint): MapPoint => [sx(p[0]), sy(p[1])]
    const trail = map.trail.map(at)
    const [a, b] = [at(map.a), at(map.b)]
    const voices = map.voices.map(at)
    const whole = map.whole.map(at)
    const region = map.region.map(at)
    return {
      trail,
      best: trail.at(-1)!,
      a,
      b,
      voices,
      region,
      whole: hull(whole),
      survey: hull(voices),
      narrowed: hull(core(region, trail.at(-1)!)),
      halves: halfPlanes(a, b),
      cut: cutLine(a, b),
    }
  }, [map])
  const { trail, best, a, b, voices, region, whole, survey, narrowed, halves, cut } = shape

  return (
    <figure className="space-y-2">
      <Legend />
      <div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full rounded-lg border bg-background"
          role="img"
          aria-label="声の地図。全体とアンケートの範囲、いまの候補の範囲、見立てと道のり、A と B と切れ目"
        >
          <defs>
            <pattern
              id="voice-map-hatch"
              width={6}
              height={6}
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <line x1={0} y1={0} x2={0} y2={6} className="stroke-foreground" strokeWidth={1} />
            </pattern>
          </defs>
          {/* 切れ目の両側をうっすら塗り分ける */}
          {(['a', 'b'] as const).map((side) => (
            <motion.polygon
              key={side}
              initial={false}
              animate={{ points: outline(halves[side]) }}
              transition={SPRING}
              fill={SIDE_COLOR[side]}
              opacity={0.07}
            />
          ))}
          {/* 全体（手持ちの声から外挿で届く範囲） */}
          <polygon
            points={outline(whole)}
            fill="none"
            className="stroke-muted-foreground"
            strokeOpacity={0.6}
            strokeWidth={1}
            strokeDasharray="4 3"
            strokeLinejoin="round"
          />
          {/* アンケートの範囲（手持ちの声が囲む範囲） */}
          <polygon
            points={outline(survey)}
            className="fill-muted stroke-muted-foreground"
            fillOpacity={0.35}
            strokeOpacity={0.5}
            strokeWidth={1}
            strokeLinejoin="round"
          />
          {/* いまの候補の範囲 */}
          <polygon
            points={outline(narrowed)}
            fill="url(#voice-map-hatch)"
            className="stroke-foreground"
            fillOpacity={0.35}
            strokeOpacity={0.6}
            strokeWidth={1.25}
            strokeLinejoin="round"
          />
          {region.map(([x, y], i) => (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={1}
              className="pointer-events-none fill-foreground"
              opacity={0.25}
            />
          ))}
          {cut && (
            <motion.line
              initial={false}
              animate={{ x1: cut[0][0], y1: cut[0][1], x2: cut[1][0], y2: cut[1][1] }}
              transition={SPRING}
              className="stroke-muted-foreground"
              strokeWidth={1.5}
              strokeDasharray="5 4"
            />
          )}
          {/* 手持ちの声 */}
          {voices.map(([x, y], i) => (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={3}
              className="fill-background stroke-muted-foreground"
              strokeWidth={1}
              strokeDasharray="2 1.5"
            />
          ))}
          {/* 見立ての道のり */}
          <motion.polyline
            initial={false}
            animate={{ points: trail.map((p) => p.join(',')).join(' ') }}
            transition={SPRING}
            fill="none"
            className="pointer-events-none stroke-foreground"
            strokeOpacity={0.45}
            strokeWidth={1.5}
            strokeLinejoin="round"
          />
          {trail.slice(0, -1).map(([x, y], i) => (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={2}
              className="pointer-events-none fill-foreground"
              opacity={0.3 + (0.4 * (i + 1)) / trail.length}
            />
          ))}
          {/* いまの見立て */}
          <motion.g initial={false} animate={{ x: best[0], y: best[1] }} transition={SPRING}>
            <circle r={4.5} className="fill-foreground stroke-background" strokeWidth={2} />
          </motion.g>
          {(['a', 'b'] as const).map((side) => {
            const [x, y] = side === 'a' ? a : b
            return (
              <motion.g key={side} initial={false} animate={{ x, y }} transition={SPRING}>
                {playing === side && (
                  <motion.circle
                    r={11}
                    fill="none"
                    stroke={SIDE_COLOR[side]}
                    strokeWidth={2}
                    animate={{ r: [11, 22], opacity: [0.7, 0] }}
                    transition={{ duration: 1.1, repeat: Infinity, ease: 'easeOut' }}
                  />
                )}
                <circle
                  r={11}
                  className="fill-background"
                  stroke={SIDE_COLOR[side]}
                  strokeWidth={2.5}
                />
                <text
                  textAnchor="middle"
                  dominantBaseline="central"
                  className="fill-foreground text-[11px] font-semibold"
                >
                  {side.toUpperCase()}
                </text>
              </motion.g>
            )
          })}
        </svg>
      </div>
    </figure>
  )
}

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {(['a', 'b'] as const).map((side) => (
        <li key={side} className="flex items-center gap-1.5">
          <span
            className="size-3 rounded-full border-2 bg-background"
            style={{ borderColor: SIDE_COLOR[side] }}
          />
          {side.toUpperCase()}
        </li>
      ))}
      <li className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-full bg-foreground" />
        見つけた声
      </li>
      <li className="flex items-center gap-1.5">
        <span className="h-px w-5 bg-foreground/50" />
        道のり
      </li>
      <li className="flex items-center gap-1.5">
        <span className="size-3 rounded-sm border border-foreground/60 bg-[repeating-linear-gradient(45deg,currentColor_0_1px,transparent_1px_4px)]" />
        いまの候補の範囲
      </li>
      <li className="flex items-center gap-1.5">
        <span className="size-3 rounded-sm border border-muted-foreground/50 bg-muted/60" />
        アンケートの範囲
      </li>
      <li className="flex items-center gap-1.5">
        <span className="size-3 rounded-sm border border-dashed border-muted-foreground" />
        全体
      </li>
      <li className="flex items-center gap-1.5">
        <span className="size-2 rounded-full border border-dashed border-muted-foreground" />
        手持ちの声
      </li>
    </ul>
  )
}

// 地図の座標を SVG の座標へ。すべての点が収まるようにし、縦横の縮尺をそろえる
// （距離が見た目どおりになるように）
function scales(map: MapData) {
  const all = [...map.whole, ...map.voices, ...map.region, ...map.trail, map.a, map.b]
  const xs = all.map((p) => p[0])
  const ys = all.map((p) => p[1])
  const [minX, maxX, minY, maxY] = [
    Math.min(...xs),
    Math.max(...xs),
    Math.min(...ys),
    Math.max(...ys),
  ]
  const k = Math.min((W - 2 * PAD) / (maxX - minX || 1), (H - 2 * PAD) / (maxY - minY || 1))
  const [cx, cy] = [(minX + maxX) / 2, (minY + maxY) / 2]
  return {
    sx: (x: number) => W / 2 + (x - cx) * k,
    sy: (y: number) => H / 2 - (y - cy) * k,
  }
}

// 切れ目（A と B の垂直二等分線）を、地図の枠いっぱいに引いた2点
function cutLine(a: MapPoint, b: MapPoint): [MapPoint, MapPoint] | null {
  const [dx, dy] = [b[0] - a[0], b[1] - a[1]]
  const len = Math.hypot(dx, dy)
  if (len < 1e-6) return null
  const [mx, my] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
  const [tx, ty] = [-dy / len, dx / len]
  const reach = W + H
  return [
    [mx - tx * reach, my - ty * reach],
    [mx + tx * reach, my + ty * reach],
  ]
}

// 地図の枠を、切れ目で A の側と B の側に切り分けた2つの多角形
function halfPlanes(a: MapPoint, b: MapPoint): Record<Side, MapPoint[]> {
  const frame: MapPoint[] = [
    [0, 0],
    [W, 0],
    [W, H],
    [0, H],
  ]
  const [nx, ny] = [b[0] - a[0], b[1] - a[1]]
  const [mx, my] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
  const f = (p: MapPoint) => (p[0] - mx) * nx + (p[1] - my) * ny
  const clip = (keep: (v: number) => boolean) => {
    const out: MapPoint[] = []
    frame.forEach((p, i) => {
      const q = frame[(i + 1) % frame.length]!
      const [fp, fq] = [f(p), f(q)]
      if (keep(fp)) out.push(p)
      if (keep(fp) !== keep(fq)) {
        const t = fp / (fp - fq)
        out.push([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])])
      }
    })
    // 多角形の頂点の数を問いのあいだでそろえる（数が変わると形の動きが崩れる）
    while (out.length < 5) out.push(out.at(-1) ?? [0, 0])
    return out
  }
  return { a: clip((v) => v < 0), b: clip((v) => v >= 0) }
}

// 点たちを囲むいちばん小さい凸多角形（Andrew の単調連鎖法）
function hull(points: readonly MapPoint[]): MapPoint[] {
  const sorted = points.toSorted((p, q) => p[0] - q[0] || p[1] - q[1])
  if (sorted.length < 3) return sorted
  return [...half(sorted), ...half(sorted.toReversed())]
}

// 凸包の片側（並べた順にたどり、左に曲がる点だけを残す）
function half(list: readonly MapPoint[]) {
  const out: MapPoint[] = []
  for (const p of list) {
    while (out.length >= 2 && cross(out.at(-2)!, out.at(-1)!, p) <= 0) out.pop()
    out.push(p)
  }
  return out.slice(0, -1)
}

function cross(o: MapPoint, p: MapPoint, q: MapPoint) {
  return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
}

// 多角形の頂点を SVG の points 属性へ
function outline(points: readonly MapPoint[]) {
  return points.map((p) => p.join(',')).join(' ')
}

// 群れの芯。見立てから近い順に CORE の割合だけ残す（離れた少数の点で輪郭が膨らまないように）
const CORE = 0.85

function core(points: readonly MapPoint[], center: MapPoint) {
  const far = (p: MapPoint) => Math.hypot(p[0] - center[0], p[1] - center[1])
  return points.toSorted((p, q) => far(p) - far(q)).slice(0, Math.ceil(points.length * CORE))
}
