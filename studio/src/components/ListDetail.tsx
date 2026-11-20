import type { ReactNode } from 'react'

// 一覧の1行。選んでいる行は中身と同じ白い面にして、右の中身とつながって見せる
export const LIST_ROW =
  'w-full rounded-lg text-left outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current]:bg-card aria-[current]:shadow-[0_0_0_1px_var(--border)]'

type Props = { header: ReactNode; list: ReactNode; children: ReactNode }

// 左に一覧、右に選んだものの中身を並べ、それぞれを別に送る画面の根。見出し（header）は送らずに上に残す
// （ページごと送ると、一覧の下の方を選ぶたびに中身が画面の外へ出て、一覧の高さも見出しの高さ次第になる）。
// data-list-detail があると共通レイアウト（routes/__root.tsx）がページを送らず画面の高さに収める。
// 一覧は地（紙）の上に、中身は1枚の白い面に置く。枠を重ねずに、いま触っているものを面で示す
export function ListDetail({ header, list, children }: Props) {
  return (
    <div data-list-detail className="flex min-h-0 flex-1 flex-col">
      {header}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(11rem,17rem)_minmax(0,1fr)] gap-5 max-md:gap-3">
        <div className="flex min-h-0 flex-col overflow-y-auto pb-4">{list}</div>
        <div className="min-h-0 overflow-y-auto rounded-t-2xl border border-b-0 bg-card px-7 pt-6 pb-16 max-md:px-4">
          {children}
        </div>
      </div>
    </div>
  )
}
