import { AudioLines, BookText, FileText, ListChecks, Speech } from 'lucide-react'

// 頻度ではなく「何を育てるか」でまとめる。頻度は使う人によって違う。
export const NAV_GROUPS = [
  {
    label: '要約',
    items: [
      { to: '/review', label: 'レビュー', icon: ListChecks, badge: 'unreviewed' },
      { to: '/prompt', label: 'プロンプト', icon: FileText },
    ],
  },
  { label: '読み', items: [{ to: '/dict', label: '辞書', icon: BookText }] },
  {
    label: '声',
    items: [
      { to: '/profiles', label: 'プロファイル', icon: AudioLines },
      { to: '/styles', label: 'スタイル', icon: Speech },
    ],
  },
] as const
