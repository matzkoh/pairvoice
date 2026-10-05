import fsp from 'node:fs/promises'

import type { OkResponse, PromptResponse } from '../../shared/api-types.ts'
import { readHistoryVersion, historyResponse, writePromptWithSnapshot } from '../history.ts'
import { badRequest, notFound, readJsonBody, sendJson } from '../http.ts'
import { HISTORY_DIR, PROMPT_FILE } from '../paths.ts'
import type { AddRoute } from '../router.ts'

export function registerPromptRoutes(addRoute: AddRoute) {
  addRoute('GET', '/api/prompt', async (req, res) => {
    let text = ''
    try {
      text = await fsp.readFile(PROMPT_FILE, 'utf8')
    } catch {
      // まだ作成されていない
    }
    const body: PromptResponse = { text }
    sendJson(res, 200, body)
  })

  addRoute('PUT', '/api/prompt', async (req, res) => {
    const body = await readJsonBody(req)
    if (typeof body.text !== 'string' || body.text.trim() === '') {
      return badRequest(res, 'text (non-empty string) is required')
    }
    await writePromptWithSnapshot(body.text)
    const response: OkResponse = { ok: true }
    sendJson(res, 200, response)
  })

  addRoute('GET', '/api/prompt/history', async (req, res) => {
    sendJson(res, 200, await historyResponse('prompt', HISTORY_DIR))
  })

  addRoute('POST', '/api/prompt/restore', async (req, res) => {
    const body = await readJsonBody(req)
    const version = await readHistoryVersion('prompt', HISTORY_DIR, body.name)
    if (version === 'invalid') return badRequest(res, 'valid history file name is required')
    if (version === 'missing') return notFound(res)
    // 復元も履歴に1件増える。「その版が再び動き出した」記録として正しい。
    await writePromptWithSnapshot(version.content)
    const response: OkResponse = { ok: true }
    sendJson(res, 200, response)
  })
}
