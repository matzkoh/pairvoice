// 2声を混ぜるスライダーの位置 s（0〜1）を、B の重み（0〜1）に直す。
//
// 重みをそのまま目盛りにすると、聞こえる声は両端の 1/4 ずつではほとんど動かず、真ん中で
// 一気に変わる（重みと声の位置は S字: p = w² / (w² + (1−w)²)）。その逆の
// w = √s / (√s + √(1−s)) で直し、スライダーの位置と聞こえる声の位置をそろえる。
// 2 は、話者ベクトルで測った4組（k = 1.4〜2.2）に当てはめた値。組ごとの残りのずれは耳で詰める
export function mixWeight(s: number) {
  const b = Math.sqrt(Math.min(Math.max(s, 0), 1))
  const a = Math.sqrt(1 - Math.min(Math.max(s, 0), 1))
  return b / (a + b)
}
