// 頻度ではなく「何を育てるか」でまとめる。頻度は使う人によって違う。
export const NAV_GROUPS = [
  {
    label: '声',
    items: [
      { to: '/profiles', label: 'プロファイル' },
      { to: '/styles', label: 'スタイル' },
    ],
  },
  { label: '読み', items: [{ to: '/dict', label: '辞書' }] },
  {
    label: '要約',
    items: [
      { to: '/review', label: 'レビュー', badge: 'unreviewed' },
      { to: '/prompt', label: 'プロンプト' },
    ],
  },
] as const
