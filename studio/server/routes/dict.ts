import fsp from 'node:fs/promises'

import type {
  DictResponse,
  DictRowData,
  DictTestResponse,
  OkResponse,
} from '../../shared/api-types.ts'
import { badRequest, isRecord, readJsonBody, sendJson } from '../http.ts'
import { DICT_FILE } from '../paths.ts'
import type { AddRoute } from '../router.ts'
import { atomicWrite } from '../storage.ts'

export function parseDictTsv(content: string): DictRowData[] {
  // 手で編集されて CRLF になっていても、行末の \r を値に混ぜない（混ざると保存し直せない）
  return content
    .split(/\r?\n/)
    .filter((line) => line.length > 0)
    .map((line) => {
      const [from = '', to = '', memo = ''] = line.split('\t')
      return { from, to, memo }
    })
}

export function serializeDictTsv(rows: readonly DictRowData[]) {
  if (rows.length === 0) return ''
  return rows.map((r) => [r.from, r.to, r.memo || ''].join('\t')).join('\n') + '\n'
}

// 空の from は全文字の間に to を差し込むので飛ばす（フックの normalize_reading も同じ）
export function applyDict(text: string, rows: readonly Pick<DictRowData, 'from' | 'to'>[]) {
  let out = text
  for (const { from, to } of rows) {
    if (!from) continue
    out = out.split(from).join(to)
  }
  return out
}

// dict.tsv はタブ区切り・改行区切りで、フック（bash）もそのまま読む。値にこれらが
// 混ざると列と行がずれ、その行から後ろの置換が壊れる
const TSV_CONTROL = /[\t\n\r]/

export function registerDictRoutes(addRoute: AddRoute) {
  addRoute('GET', '/api/dict', async (req, res) => {
    let raw = ''
    try {
      raw = await fsp.readFile(DICT_FILE, 'utf8')
    } catch {
      // dict.tsvがまだ無い場合は空扱い
    }
    const body: DictResponse = { rows: parseDictTsv(raw) }
    sendJson(res, 200, body)
  })

  addRoute('PUT', '/api/dict', async (req, res) => {
    const body = await readJsonBody(req)
    const rows = Array.isArray(body.rows) ? body.rows : null
    if (!rows) return badRequest(res, 'rows array is required')
    for (const r of rows) {
      if (
        !isRecord(r) ||
        typeof r.from !== 'string' ||
        typeof r.to !== 'string' ||
        typeof r.memo !== 'string'
      ) {
        return badRequest(res, 'each row needs from/to/memo strings')
      }
      if (r.from.trim() === '') {
        return badRequest(res, 'from must not be empty')
      }
      if ([r.from, r.to, r.memo].some((v) => TSV_CONTROL.test(v))) {
        return badRequest(res, 'from/to/memo must not contain tabs or line breaks')
      }
    }
    await atomicWrite(DICT_FILE, serializeDictTsv(rows))
    const response: OkResponse = { ok: true }
    sendJson(res, 200, response)
  })

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
