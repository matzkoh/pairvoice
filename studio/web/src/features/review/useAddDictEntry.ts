import { useQueryClient } from '@tanstack/react-query'

import { dictQueryOptions } from '@/features/dict/queries'
import { upsertDictRow } from '@/features/dict/upsertDictRow'
import { apiSend } from '@/lib/api'
import type { DictResponse, DictRowData } from '@/lib/api-types'

export function useAddDictEntry(): (entry: DictRowData) => Promise<void> {
  const queryClient = useQueryClient()
  return async (entry) => {
    // PUT /dict は全置換。手元のキャッシュに足すと、別の場所（別タブの辞書画面など）で
    // 保存した行を巻き戻しうるので、書く直前にサーバーから取り直す
    const latest = await queryClient.query({ ...dictQueryOptions(), staleTime: 0 })
    const rows = upsertDictRow(latest.rows, entry)
    await apiSend('/dict', 'PUT', { rows })
    // invalidate だけだと、次に辞書画面を開いたとき再取得が終わる前の古い rows で表が
    // 初期化され、そのまま保存すると今足した行が消える。書いた内容をそのまま置く
    queryClient.setQueryData<DictResponse>(['dict'], { rows })
  }
}
