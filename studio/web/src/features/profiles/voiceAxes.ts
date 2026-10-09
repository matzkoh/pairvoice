// 声の軸（性別・年代・高さ・声質・雰囲気・速さ）と、その段階から caption を組む。
// 事前の質問で答えてもらう項目と、2択で声を作る caption に使う。

export type Level = { label: string; phrase: string }
export type Axis = { key: string; label: string; levels: readonly Level[] }

// 段階は聴こえ方の連続に沿って並べる（事前の質問の絵も、段階の番号から連続に描く）
export const AXES: readonly Axis[] = [
  {
    key: 'gender',
    label: '性別',
    levels: [
      // 中性的を挟むと、女性を選んだ直後に「中性的か女性か」をもう一度聞くことになる
      { label: '男性', phrase: '男性' },
      { label: '女性', phrase: '女性' },
    ],
  },
  {
    key: 'age',
    label: '年代',
    levels: [
      { label: '十代', phrase: '十代の' },
      { label: '二十代', phrase: '二十代の' },
      { label: '三十代', phrase: '三十代の' },
      { label: '四十代', phrase: '四十代の' },
      { label: '年配', phrase: '年配の' },
    ],
  },
  {
    key: 'pitch',
    label: '高さ',
    levels: [
      { label: 'とても低い', phrase: '声はとても低い' },
      { label: '低め', phrase: '声は低め' },
      { label: '普通', phrase: '声の高さは普通' },
      { label: '高め', phrase: '声は高め' },
      { label: 'とても高い', phrase: '声はとても高い' },
    ],
  },
  {
    key: 'texture',
    label: '声質',
    levels: [
      { label: '息まじり', phrase: '息まじりの柔らかい声' },
      { label: '丸い', phrase: '丸く温かい声' },
      { label: '澄んだ', phrase: '澄んだ声' },
      { label: 'はきはき', phrase: 'はきはきした声' },
      { label: '力強い', phrase: '張りのある力強い声' },
    ],
  },
  {
    key: 'mood',
    label: '雰囲気',
    levels: [
      { label: '淡々', phrase: '淡々とした調子で' },
      { label: '落ち着き', phrase: '落ち着いた調子で' },
      { label: '優しい', phrase: '穏やかで優しい調子で' },
      { label: '明るい', phrase: '明るい調子で' },
      { label: '元気', phrase: '元気いっぱいの調子で' },
    ],
  },
  {
    key: 'speed',
    label: '速さ',
    levels: [
      { label: 'ゆっくり', phrase: 'ゆっくり' },
      { label: 'ややゆっくり', phrase: 'ややゆっくり' },
      { label: '普通', phrase: '普通の速さで' },
      { label: 'やや速め', phrase: 'やや速めに' },
      { label: '速い', phrase: '速く' },
    ],
  },
]

// gender 軸で女性を表す段階
export const FEMALE = 1

// 軸ごとの段階の番号。答えていない軸は入らない
export type Point = Partial<Record<string, number>>

// caption を組む。答えていない軸は書かない（モデルに任せる）
export function captionFor(point: Point): string {
  const phrase = (key: string) => {
    const level = point[key]
    return level === undefined ? undefined : AXES.find((a) => a.key === key)!.levels[level]!.phrase
  }
  const who = `${phrase('age') ?? ''}${phrase('gender') ?? '人'}の声。`
  const sound = [phrase('pitch'), phrase('texture')].filter(Boolean).join('、')
  const style = [phrase('mood'), phrase('speed')].filter(Boolean).join('、')
  return who + (sound ? `${sound}。` : '') + (style ? `${style}話す。` : '')
}

// 答えた軸はそのまま、答えていない軸はばらばらの段階にした点。2択で A と B が寄る先の
// でたらめな声の caption に使う（答えた範囲の中で、どの向きにも振れるように）
export function randomPoint(known: Point, random = Math.random): Point {
  return Object.fromEntries(
    AXES.map((axis) => [axis.key, known[axis.key] ?? Math.floor(random() * axis.levels.length)]),
  )
}
