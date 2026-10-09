import type { DictRowData, DictTestResponse } from '../../shared/api-types.ts'
import { badRequest, isRecord, readJsonBody, sendJson } from '../http.ts'
import type { AddRoute } from '../router.ts'

// 空の from は全文字の間に to を差し込むので飛ばす（常駐サーバーの reading.apply_dict も同じ）
export function applyDict(text: string, rows: readonly Pick<DictRowData, 'from' | 'to'>[]) {
  let out = text
  for (const { from, to } of rows) {
    if (!from) continue
    out = out.split(from).join(to)
  }
  return out
}

// 読み辞書の読み書きは pairvoice の API が持つ。ここは保存せずに試すプレビューだけ
export function registerDictRoutes(addRoute: AddRoute) {
  addRoute('POST', '/api/dict/test', async (req, res) => {
    const body = await readJsonBody(req)
    if (typeof body.text !== 'string' || !Array.isArray(body.rows)) {
      return badRequest(res, 'text (string) and rows (array) are required')
    }
    if (
      !body.rows.every(
        (r: unknown) => isRecord(r) && typeof r.from === 'string' && typeof r.to === 'string',
      )
    ) {
      return badRequest(res, 'each row needs from/to strings')
    }
    const response: DictTestResponse = { result: applyDict(body.text, body.rows) }
    sendJson(res, 200, response)
  })
}
