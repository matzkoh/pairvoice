#!/usr/bin/env node
import http from 'node:http'
import { fileURLToPath } from 'node:url'

import { createStudioServer } from './server/app.ts'
import { DEFAULT_PORT } from './server/paths.ts'

// テストが取り出す名前。定数は server/paths.ts が読み込み時に確定する
export * from './server/paths.ts'
export { currentPromptSince, parseCorpusTs, parseHistoryTs } from './server/history.ts'
export { applyDict } from './server/routes/dict.ts'
export { makeAudioPathResolver } from './server/storage.ts'

export function startServer(port = DEFAULT_PORT) {
  const server = createStudioServer()
  return new Promise<http.Server>((resolve) => {
    server.listen(port, '127.0.0.1', () => resolve(server))
  })
}

const isMain = process.argv[1] === fileURLToPath(import.meta.url)
if (isMain) {
  const args = process.argv.slice(2)
  let port = DEFAULT_PORT
  let shouldOpen = false
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--port') port = Number(args[++i])
    if (args[i] === '--open') shouldOpen = true
  }
  // DIST_DIR は pnpm build が作る成果物なのでここでは作らない（無ければ 503 で案内する）。
  const server = await startServer(port)
  // 127.0.0.1 に listen した直後なので address は AddressInfo。--port 0 を渡したときの
  // 実ポートを出すために読む（型の上でだけ string | null もありうるので、その場合は指定値）。
  const address = server.address()
  const actualPort = address !== null && typeof address !== 'string' ? address.port : port
  console.log(`speak-summary studio: http://127.0.0.1:${actualPort}`)
  if (shouldOpen) {
    const { execFile } = await import('node:child_process')
    execFile('open', [`http://127.0.0.1:${actualPort}`])
  }
}
