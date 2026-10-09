import fsp from 'node:fs/promises'

import type { OkResponse, StyleData, StylesResponse } from '../../shared/api-types.ts'
import { badRequest, isRecord, readJsonBody, sendJson } from '../http.ts'
import { STYLES_FILE } from '../paths.ts'
import type { AddRoute } from '../router.ts'
import { atomicWrite } from '../storage.ts'

// 項目の名前と値の型の細かい検査は常駐サーバー（styles.py）が持つ。ここで断るのは、
// 1件の誤りで styles.json 全体が読めなくなる形の崩れだけ。崩れていれば理由の文字列を返す
function parseStyles(value: unknown): StyleData[] | string {
  if (!Array.isArray(value)) return 'styles array is required'
  const styles: StyleData[] = []
  for (const style of value) {
    if (!isRecord(style) || typeof style.name !== 'string' || style.name.trim() === '') {
      return 'each style needs a non-empty name'
    }
    const name = style.name.trim()
    // /speak は名前で引くので、重なると後ろのスタイルに届かない
    if (styles.some((s) => s.name === name)) return `duplicate style name: ${name}`
    const { caption, sampler } = style
    if (caption !== null && typeof caption !== 'string') return 'caption must be a string or null'
    if (!isRecord(sampler)) return 'sampler must be an object'
    if (!Object.values(sampler).every((v) => ['number', 'string', 'boolean'].includes(typeof v))) {
      return 'sampler values must be numbers, strings or booleans'
    }
    styles.push({ name, caption, sampler })
  }
  return styles
}

export function registerStyleRoutes(addRoute: AddRoute) {
  addRoute('GET', '/api/styles', async (req, res) => {
    let styles: StyleData[] = []
    try {
      styles = JSON.parse(await fsp.readFile(STYLES_FILE, 'utf8')).styles ?? []
    } catch {
      // まだ無い（壊れていれば、保存し直すと直る）
    }
    const body: StylesResponse = { styles }
    sendJson(res, 200, body)
  })

  addRoute('PUT', '/api/styles', async (req, res) => {
    const body = await readJsonBody(req)
    const styles = parseStyles(body.styles)
    if (typeof styles === 'string') return badRequest(res, styles)
    await atomicWrite(STYLES_FILE, JSON.stringify({ styles }, null, 2) + '\n')
    const response: OkResponse = { ok: true }
    sendJson(res, 200, response)
  })
}
