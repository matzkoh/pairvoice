import type { ReactNode } from 'react'

type Props = { header: ReactNode; list: ReactNode; children: ReactNode }

// 左に一覧、右に選んだものの中身を並べ、それぞれを別に送る画面の根。見出し（header）は送らずに上に残す
// （ページごと送ると、一覧の下の方を選ぶたびに中身が画面の外へ出て、一覧の高さも見出しの高さ次第になる）。
// data-list-detail があると共通レイアウト（routes/__root.tsx）がページを送らず画面の高さに収める
export function ListDetail({ header, list, children }: Props) {
  return (
    <div data-list-detail className="flex min-h-0 flex-1 flex-col">
      {header}
      <div className="grid min-h-0 flex-1 grid-cols-[16rem_minmax(0,1fr)] gap-6">
        <div className="flex min-h-0 flex-col overflow-y-auto pb-4">{list}</div>
        {/* 枠のフォーカスリングが送りの縁で欠けないよう左に少し、スクロールバーが中身に重ならないよう
            右はページの余白まで広げる */}
        <div className="-mr-6 -ml-1 min-h-0 overflow-y-auto pr-6 pb-16 pl-1">{children}</div>
      </div>
    </div>
  )
}
