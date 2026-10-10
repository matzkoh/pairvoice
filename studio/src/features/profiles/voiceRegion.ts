// 2択を繰り返して、望みの声を探す（候補の範囲を半分ずつに切る）。
//
// 候補の範囲は、手持ちのきれいな声（caption から作った声）たちの混ぜ合わせの点の群れとして持つ。
// 手持ちの声はアンケートの範囲で作るので、混ぜ合わせに少しの外挿を加えた全体の中を探し、
// アンケートの範囲の少し外にある声にも届くようにする。
//
// 毎問、群れの中心をいまの見立てにする。A と B は、見立てを手持ちの声1本へ大きく寄せた声の
// 組のうち、2つから等しく離れた面が群れをほぼ半分に切り、群れの広がる向きに沿って最も離れた組に
// する。どちらも理想の声ではないがはっきり違うので迷わず選べ、答えで群れは半分に絞られる。
// 範囲の端の2本を機械的に選ぶと、範囲が隅に寄ったとき毎回同じ組になり、面が範囲を通らずに
// 切れなくなる。見立てを挟んで対称に離すと、外挿の上限で2つを十分に離せない。
//
// 選び間違いはありうるので、反対側の点も消さずに重みを下げるだけにする（答えが重なれば、
// 間違った答えの分は他の答えで打ち消される）。手持ちの声は聞いている間に増やし、答えるたびに
// 全体から撒き直した点を、それまでの答えすべてに照らして群れに入れる。
//
// 声の位置は話者ベクトル（モデル自身の声の物差し）で測る。混ぜた声の話者ベクトルは、
// 重みに沿って混ぜた話者ベクトルとほぼ同じになることを確かめてある。声はどれも、caption から
// 作ったきれいな声たちの混ぜ具合として持つ（合成した音声から表現を取り直すと音質が落ちる）。

// vector は長さ1にそろえた話者ベクトル（voiceOf で作る）
export type Voice = { path: string; vector: readonly number[] }
export type Side = 'a' | 'b'
export type Choice = Side | 'skip'

export function voiceOf(path: string, vector: readonly number[]): Voice {
  return { path, vector: normalize(vector) }
}

export type MixPart = { audio: string; weight: number }

// 手持ちの声の重み（voices と同じ並び）
export type Weights = readonly number[]

// 1回の答え。A と B の話者ベクトルと、選んだ側
export type Answer = {
  a: readonly number[]
  b: readonly number[]
  choice: Choice
  pair: readonly [number, number]
}

// 候補の範囲。points は手持ちの声の混ぜ合わせ（和が1。全体の中）の点の群れ
export type Region = {
  voices: readonly Voice[]
  points: readonly Weights[]
  answers: readonly Answer[]
  seed: number
}

// 1回の2択。center はいまの見立て
// pair は A と B が寄った手持ちの声の番号
export type Round = { center: Weights; a: Weights; b: Weights; pair: readonly [number, number] }

// 群れの点の数
const POINTS = 600
// 答えるたびに撒き直す点の数
const FRESH = 2000
// 点の散らし方。1 より小さいほど、少数の声の混ぜ合わせ（個性の残る声）に寄せる
const ALPHA = 0.3
// 答えの確かさ。A と B の話者ベクトルの距離の2乗の差（声どうしのふつうの距離の2乗が単位）に
// 掛ける。大きいほど、答えに沿って群れを鋭く切る
const SHARPNESS = 12
// 全体。手持ちの声の混ぜ合わせに、負の重みの合計がこれまでの外挿を加えた範囲。手持ちの声は
// アンケートの範囲で作るので、その少し外まで探せる。外挿が大きいほど声が崩れる
const REACH = 0.5
// A と B を、見立てから手持ちの声へどれだけ寄せるか。大きいほどはっきり違う
const PROBE = 0.7
// A と B が群れをどれだけ均等に切るか。片側に入る点の割合がこの幅を外れる組は選ばない
const BALANCE = 0.2
// 混ぜる重みのうち、これより小さいものは混ぜない（聞いて分かる違いにならず、合成の手間が増える）
const MIN_WEIGHT = 0.02
// 答えるたびに点を揺らす回数と幅（同じ点ばかりに偏らないように）
const MOVES = 6
const MOVE_SIZE = 0.15

export function regionOf(voices: readonly Voice[], seed: number): Region {
  const random = rng(seed)
  return {
    voices,
    points: Array.from({ length: POINTS }, () => scatter(voices.length, random)),
    answers: [],
    seed,
  }
}

// 手持ちの声が増えたら群れに加える。前からある点では、新しい声の重みは 0 から始まり、
// 答えるたびの揺らしで流れ込む
export function withVoices(region: Region, voices: readonly Voice[]): Region {
  if (voices.length === region.voices.length) return region
  return { ...region, voices, points: region.points.map((p) => pad(p, voices.length)) }
}

// いまの見立て（群れの中心）と、A と B
export function roundOf(region: Region): Round {
  const { voices, points } = region
  const center = mean(points)
  const { gram } = spaceOf(voices)
  const direction = widest(points, center, gram, rng(region.seed + 7919 * region.answers.length))
  // 見立てを手持ちの声1本へ大きく寄せた声を候補にし、2つを分ける面が群れをほぼ半分に切る
  // 組のうち、群れの広がる向きに沿って最も離れた組を A と B にする。前の問いと同じ組は避ける
  const candidates = voices.map((_, i) =>
    center.map((c, k) => (1 - PROBE) * c + (k === i ? PROBE : 0)),
  )
  const along = candidates.map((w) => quadOf(gram, w, direction))
  const last = region.answers.at(-1)
  const sample = sampleOf(region)
  const share = (a: Weights, b: Weights) => sample.filter(halfOf(gram, a, b)).length / sample.length
  let best: [number, number] | null = null
  let bestGain = 0
  for (let i = 0; i < voices.length; i++) {
    for (let j = 0; j < voices.length; j++) {
      const gain = along[i]! - along[j]!
      if (gain <= bestGain) continue
      if (last && last.pair[0] === i && last.pair[1] === j) continue
      if (Math.abs(share(candidates[i]!, candidates[j]!) - 0.5) > BALANCE) continue
      ;[best, bestGain] = [[i, j], gain]
    }
  }
  // 半分に切れる組がないときは、群れの広がる向きの両端の組
  best ??= [argmax(along), argmin(along)]
  return { center, a: candidates[best[0]]!, b: candidates[best[1]]!, pair: best }
}

// 点が A の側にあるか（A と B から等しく離れた面のどちら側か）。重みのまま、手持ちの声どうしの
// 内積 G で測る: |x − b|² − |x − a|² = 2 xᵀG(a − b) + bᵀGb − aᵀGa
function halfOf(gram: number[][], a: Weights, b: Weights) {
  const g = gram.map((row) => dot(row, a) - dot(row, b))
  const c = (quad(gram, b) - quad(gram, a)) / 2
  return (p: Weights) => dot(p, g) + c > 0
}

// 答えを群れに入れる。選ばなかった側の点の重みを下げて引き直し、少し揺らす
export function answer(region: Region, round: Round, choice: Choice): Region {
  const { voices } = region
  const n = voices.length
  const answers = [
    ...region.answers,
    {
      a: combine(voices, pad(round.a, n)),
      b: combine(voices, pad(round.b, n)),
      choice,
      pair: round.pair,
    },
  ]
  const fits = fitter(voices, answers)
  const random = rng(region.seed + 104729 * answers.length)
  // 手持ちの声すべてから撒き直した点と、これまでの点を合わせ、答えすべてに合う度合いで
  // 引き直す（後から加わった声も、答えに合えば群れに入る）
  const candidates = [...region.points, ...Array.from({ length: FRESH }, () => scatter(n, random))]
  const scores = candidates.map(fits)
  const top = Math.max(...scores)
  const weights = scores.map((f) => Math.exp(f - top))
  const total = weights.reduce((s, w) => s + w, 0)
  let points = region.points.map(() => candidates[pick(weights, total * random())]!)
  // それまでの答えすべてに合う向きへ、点を少しずつ揺らす（メトロポリス法）
  points = points.map((p) => {
    let [cur, fit] = [p, fits(p)]
    for (let k = 0; k < MOVES; k++) {
      const next = nudge(cur, random)
      if (!next) continue
      const nextFit = fits(next)
      if (Math.log(random()) < nextFit - fit) [cur, fit] = [next, nextFit]
    }
    return cur
  })
  return { ...region, points, answers }
}

export function mixOf(voices: readonly Voice[], weights: Weights): MixPart[] {
  const kept = voices
    .map((voice, i) => ({ audio: voice.path, weight: weights[i] ?? 0 }))
    .filter((part) => Math.abs(part.weight) >= MIN_WEIGHT)
  const total = kept.reduce((s, part) => s + part.weight, 0)
  return kept.map((part) => ({ ...part, weight: rounded(part.weight / total) }))
}

// 混ぜた声の見込みの話者ベクトル
export function combine(voices: readonly Voice[], weights: Weights) {
  return weighted(
    voices.map((v) => v.vector),
    voices.map((_, k) => weights[k] ?? 0),
  )
}

// 群れの広がり（話者ベクトルでの、中心からの距離の2乗平均の平方根）
export function spreadOf(region: Region) {
  const { gram } = spaceOf(region.voices)
  const center = mean(region.points)
  const sum = region.points.reduce((s, p) => s + quad(gram, sub(p, center)), 0)
  return Math.sqrt(Math.max(0, sum / region.points.length))
}

// 点 p が答えたちに合う度合い（対数）。A と B から等しく離れた面のどちら側か、どれだけ離れて
// いるか。どちらとも言えないと答えた2択では、その面の近くほど合う
function fitter(voices: readonly Voice[], answers: readonly Answer[]) {
  const { scale } = spaceOf(voices)
  const terms = answers.map(({ a, b, choice }) => {
    const diff = sub(a, b)
    return {
      g: voices.map((v) => dot(v.vector, diff)),
      c: (dot(b, b) - dot(a, a)) / 2,
      choice,
    }
  })
  return (p: Weights) =>
    terms.reduce((s, { g, c, choice }) => {
      // |x − b|² − |x − a|² の半分。A に近いほど正
      const lean = (SHARPNESS * (dot(p, g) + c)) / scale
      if (choice === 'skip') return s - (lean * lean) / 2
      return s + logSigmoid(choice === 'a' ? lean : -lean)
    }, 0)
}

// 群れがいちばん広がっている向き（話者ベクトルで測った主成分）を、重みの向きで返す。
// 共分散 C と話者ベクトルの内積 G で、d ← C G d を繰り返す（G の上での主成分）
function widest(
  points: readonly Weights[],
  center: Weights,
  gram: number[][],
  random: () => number,
) {
  const n = center.length
  const rows = points.map((p) => sub(p, center))
  let d = sub(dirichlet(n, random, 1), center)
  for (let it = 0; it < 40; it++) {
    const gd = gram.map((row) => dot(row, d))
    const next = weighted(
      rows,
      rows.map((r) => dot(r, gd)),
    )
    const len = Math.sqrt(quad(gram, next))
    if (len < 1e-12) break
    d = next.map((v) => v / len)
  }
  return d
}

// 手持ちの声の話者ベクトルどうしの内積と、声どうしのふつうの距離の2乗（2つずつの距離の
// 平均の2乗）。手持ちの声の並びが変わるまで使い回す
type Space = { gram: number[][]; scale: number }
const spaces = new WeakMap<readonly Voice[], Space>()

function spaceOf(voices: readonly Voice[]): Space {
  const cached = spaces.get(voices)
  if (cached) return cached
  const gram = voices.map((x) => voices.map((y) => dot(x.vector, y.vector)))
  let [sum, count] = [0, 0]
  gram.forEach((row, i) => {
    for (let j = i + 1; j < row.length; j++) {
      sum += Math.sqrt(Math.max(0, 2 - 2 * row[j]!))
      count++
    }
  })
  const space = { gram, scale: count > 0 ? (sum / count) ** 2 : 1 }
  spaces.set(voices, space)
  return space
}

function project(frame: Frame, x: readonly number[]) {
  const d = sub(x, frame.origin)
  return [dot(d, frame.axes[0]), dot(d, frame.axes[1])] as const
}

export type MapPoint = readonly [number, number]

// 1回の2択と、そのときの候補の範囲
export type Step = { region: Region; round: Round }

export type MapData = {
  // 手持ちの声（アンケートの範囲のふち）
  voices: MapPoint[]
  // 全体のふち（手持ちの声から外挿で届く点）
  whole: MapPoint[]
  // いまの候補の範囲（群れの点の一部）
  region: MapPoint[]
  // これまでの見立て（最初から今まで）
  trail: MapPoint[]
  a: MapPoint
  b: MapPoint
}

// 群れから間引いて使う点の数（地図に描く点と、A と B の組が群れをどう切るか数える点）
const SAMPLE_POINTS = 160

export function mapOf(steps: readonly Step[]): MapData {
  const last = steps.at(-1)!
  const { region, round } = last
  const frame = frameOf(last)
  // 写像は線形で重みの和は1なので、声を先に地図へ写してから重みで混ぜる
  const projectorOf = (voices: readonly Voice[]) => {
    const xs = voices.map((v) => project(frame, v.vector))
    const [px, py] = [xs.map((p) => p[0]), xs.map((p) => p[1])]
    return (w: Weights): MapPoint => [dot(w, px), dot(w, py)]
  }
  const at = projectorOf(region.voices)
  const voices = region.voices.map((v) => project(frame, v.vector))
  // 全体の角は (1 + REACH)·vᵢ − REACH·vⱼ
  const whole = voices.flatMap((p, i) =>
    voices
      .filter((_, j) => j !== i)
      .map((q): MapPoint => [(1 + REACH) * p[0] - REACH * q[0], (1 + REACH) * p[1] - REACH * q[1]]),
  )
  return {
    voices,
    whole,
    region: sampleOf(region).map(at),
    trail: steps.map((s) => projectorOf(s.region.voices)(s.round.center)),
    a: at(round.a),
    b: at(round.b),
  }
}

// 地図の座標系。いまの見立てを原点に、横軸を B から A への向きにする（A と B を分ける面は
// 縦の線になり、群れのどちら側が残るかが見たとおりになる）。縦軸は、それと直交する向きのうち
// 群れがいちばん広がっている向き。問いごとに切る向きが変わるので、座標系も問いごとに変わる
type Frame = { origin: readonly number[]; axes: readonly [readonly number[], readonly number[]] }

function frameOf({ region, round }: Step): Frame {
  const { voices } = region
  const origin = combine(voices, round.center)
  const across = normalize(sub(combine(voices, round.a), combine(voices, round.b)))
  const rows = sampleOf(region).map((p) => sub(combine(voices, p), origin))
  const off = (x: readonly number[]) =>
    sub(
      x,
      across.map((v) => v * dot(x, across)),
    )
  // 散らばりがいちばん大きい向き（べき乗法）。縦軸の上下は、1人目の声が上に来る向きにそろえる
  let up = normalize(off(sub(voices[0]!.vector, origin)))
  for (let it = 0; it < 30; it++) {
    const next = off(
      weighted(
        rows,
        rows.map((x) => dot(x, up)),
      ),
    )
    if (dot(next, next) < 1e-18) break
    up = normalize(next)
  }
  if (dot(up, sub(voices[0]!.vector, origin)) < 0) up = up.map((v) => -v)
  return { origin, axes: [across, up] }
}

// 群れから間引いた点
function sampleOf(region: Region) {
  const every = Math.max(1, Math.floor(region.points.length / SAMPLE_POINTS))
  return region.points.filter((_, i) => i % every === 0)
}

// 問いごとの候補の広がり。最初の問いを 1 とする。答えるたびに狭まる
export function spreadsOf(steps: readonly Step[]) {
  const first = spreadOf(steps[0]!.region)
  return steps.map((s) => (first > 0 ? spreadOf(s.region) / first : 0))
}

// Σ weights[k]·rows[k]
function weighted(rows: readonly (readonly number[])[], weights: readonly number[]) {
  return Array.from({ length: rows[0]!.length }, (_, i) =>
    rows.reduce((acc, row, k) => acc + weights[k]! * row[i]!, 0),
  )
}

// 2つの声のあいだで重みを少し移す。全体の外に出たら null
function nudge(p: Weights, random: () => number): Weights | null {
  const n = p.length
  if (n < 2) return null
  const i = Math.floor(random() * n)
  const j = (i + 1 + Math.floor(random() * (n - 1))) % n
  const delta = (2 * random() - 1) * MOVE_SIZE
  const next = [...p]
  next[i]! += delta
  next[j]! -= delta
  return negative(next) > REACH ? null : next
}

// 全体に散らした点。手持ちの声の混ぜ合わせを、均等な混ぜ合わせを中心に (1 + REACH) 倍に広げる
function scatter(n: number, random: () => number) {
  return dirichlet(n, random).map((w) => (1 + REACH) * w - REACH / n)
}

function negative(w: Weights) {
  return -w.reduce((s, v) => s + Math.min(0, v), 0)
}

function mean(points: readonly Weights[]) {
  return weighted(
    points,
    points.map(() => 1 / points.length),
  )
}

function quad(gram: number[][], d: Weights) {
  return quadOf(gram, d, d)
}

// xᵀ G y
function quadOf(gram: number[][], x: Weights, y: Weights) {
  return gram.reduce((s, row, i) => s + x[i]! * dot(row, y), 0)
}

function argmax(xs: readonly number[]) {
  return xs.reduce((top, v, i) => (v > xs[top]! ? i : top), 0)
}

function argmin(xs: readonly number[]) {
  return xs.reduce((low, v, i) => (v < xs[low]! ? i : low), 0)
}

function pad(p: Weights, n: number) {
  return p.length >= n ? p : [...p, ...Array.from({ length: n - p.length }, () => 0)]
}

// 重みつきで1つ選ぶ（累積が at を超えた最初の添字）
function pick(weights: readonly number[], at: number) {
  let acc = 0
  for (let i = 0; i < weights.length; i++) {
    acc += weights[i]!
    if (acc >= at) return i
  }
  return weights.length - 1
}

function dirichlet(n: number, random: () => number, alpha = ALPHA) {
  const e = Array.from({ length: n }, () => gamma(alpha, random))
  const total = e.reduce((s, v) => s + v, 0)
  return e.map((v) => v / total)
}

// ガンマ分布（Marsaglia–Tsang。alpha < 1 は alpha + 1 から引いて縮める）
function gamma(alpha: number, random: () => number): number {
  if (alpha < 1) return gamma(alpha + 1, random) * random() ** (1 / alpha)
  const d = alpha - 1 / 3
  const c = 1 / Math.sqrt(9 * d)
  for (;;) {
    let x: number
    let v: number
    do {
      x = Math.sqrt(-2 * Math.log(1 - random())) * Math.cos(2 * Math.PI * random())
      v = 1 + c * x
    } while (v <= 0)
    v = v * v * v
    const u = random()
    if (Math.log(u) < 0.5 * x * x + d - d * v + d * Math.log(v)) return d * v
  }
}

function logSigmoid(x: number) {
  return x >= 0 ? -Math.log1p(Math.exp(-x)) : x - Math.log1p(Math.exp(x))
}

// 決まった種から決まった並びを返す乱数（mulberry32）。同じ答えなら同じ群れになる
function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function rounded(x: number) {
  return Number(x.toFixed(3))
}

function sub(x: readonly number[], y: readonly number[]) {
  return x.map((v, i) => v - y[i]!)
}

// 内積
export function dot(x: readonly number[], y: readonly number[]) {
  let total = 0
  for (let i = 0; i < x.length; i++) total += x[i]! * y[i]!
  return total
}

export function normalize(x: readonly number[]) {
  const norm = Math.sqrt(dot(x, x))
  return norm > 0 ? x.map((v) => v / norm) : [...x]
}
