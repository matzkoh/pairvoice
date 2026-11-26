import type { ProfileItem } from '@/lib/api-types'

// 声の持ち方。作り方（source）ではなく、話者の表現を保存しているかで決まる。
// 利用者から見た良し悪し（音が落ちるか、合成モデルを替えても同じ声か）はこれで分かれる
export type Holding = 'random' | 'speaker' | 'reference'

export function holdingOf(item: ProfileItem): Holding {
  if (item.source === 'random') return 'random'
  return item.speaker_model ? 'speaker' : 'reference'
}

export const HOLDINGS: Record<Holding, { label: string; pro: string; con: string }> = {
  speaker: {
    label: '声をそのまま保存',
    pro: '聞いた声のまま、音を落とさずに読む',
    con: '合成モデルを替えると、参照音声から写した近い声になる',
  },
  reference: {
    label: '参照音声から写す',
    pro: '合成モデルを替えても、同じ参照音声から写して読める',
    con: '読むたびに参照音声から写すので、聞いた音とわずかに違う声になる',
  },
  random: {
    label: 'ランダム',
    pro: '作った声を束ね、読み上げのたびに1つを選ぶ',
    con: '',
  },
}

// 良し悪しを1文に
export function holdingText(holding: Holding) {
  const { pro, con } = HOLDINGS[holding]
  return con ? `${pro}。${con}。` : `${pro}。`
}

// 一覧の見出しの順。ランダムの声はほかの声を束ねるものなので先頭に置く
export const HOLDING_ORDER: readonly Holding[] = ['random', 'speaker', 'reference']
