import type { CorpusItem, CorpusResponse } from '../../shared/api-types.ts'
import { currentPromptSince, parseCorpusTs } from '../history.ts'
import { badRequest, notFound, readJsonBody, sendJson, streamWav } from '../http.ts'
import { ARCHIVES_FILE, CORPUS_FILE, REVIEWS_FILE } from '../paths.ts'
import type { AddRoute } from '../router.ts'
import { appendJsonl, readJsonlSafe, resolveAudioPath } from '../storage.ts'

// 追記専用の JSONL の1行。どれも studio の外（フック・過去の studio）が書いたものを
// 読むので、型は「書き手が守っている形」の宣言であって検証ではない。読む側が中身を
// 疑う必要のあるところ（空文字・欠損）は、これまでどおりコードで確かめている。
type CorpusRecord = {
  ts: string
  message_id: string
  input: string
  summary: string
  audio_path?: string
}

type ReviewRecord = {
  ts: string
  message_id: string
  verdict: 'good' | 'bad' | 'none'
  ideal?: string
}

type ArchiveRecord = {
  ts: string
  message_id: string
  // 書くのは studio 自身（POST /api/archives が boolean を確かめてから書く）だが、
  // 読む側は壊れた行が来たときに解除へ倒すため `=== true` で見る。読み取り側の
  // 扱いに型を合わせておく（boolean と宣言すると、その防御が冗長な比較に見える）。
  archived: unknown
}

function latestReviewsByMessageId(reviews: readonly ReviewRecord[]) {
  const map = new Map<string, ReviewRecord>()
  for (const r of reviews) {
    if (!r.message_id) continue
    map.set(r.message_id, r) // reviews.jsonlは追記専用なので、後の行ほど新しい
  }
  return map
}

// 「古い」と言うには時刻の証拠が要る。履歴が無い（境界が引けない）ときと ts が
// 読めないときは、どちらも証拠が無いので古い側に落とさない。記録を勝手に隠すより、
// 見えたまま人が判断できる方に倒す。
function isStaleReading(entry: CorpusRecord, promptSince: number | null) {
  if (promptSince === null) return false
  const ms = parseCorpusTs(entry.ts)
  return ms !== null && ms < promptSince
}

async function latestArchivedSet() {
  const archives = await readJsonlSafe<ArchiveRecord>(ARCHIVES_FILE)
  const archived = new Set<string>()
  for (const a of archives) {
    if (!a.message_id) continue
    // archives.jsonlは追記専用なので、後の行ほど新しい
    if (a.archived === true) archived.add(a.message_id)
    else archived.delete(a.message_id)
  }
  return archived
}

function isValidMessageId(id: unknown) {
  return typeof id === 'string' && id !== ''
}

export function registerCorpusRoutes(addRoute: AddRoute) {
  // review タブ（web/src/features/review/queries.ts）が全件を読むために limit=500 で
  // ループ呼び出しする。フィルタ・検索のクエリは受け付けない — 絞り込みと検索はクライアント
  // 側が全件に対して行う前提。ここにクエリパラメータを増やすと「取得できた分だけ」が母集団になり、件数表示と検索が壊れる。
  addRoute('GET', '/api/corpus', async (req, res, ctx) => {
    const [corpus, reviews, archived, promptSince] = await Promise.all([
      readJsonlSafe<CorpusRecord>(CORPUS_FILE),
      readJsonlSafe<ReviewRecord>(REVIEWS_FILE),
      latestArchivedSet(),
      currentPromptSince(),
    ])
    const latestReview = latestReviewsByMessageId(reviews)
    const newestFirst = corpus.toReversed() // corpus.jsonlは追記順（古い→新しい）
    // 負の値や小数を slice に渡すと末尾から数えた範囲になるので、0 以上の整数に丸める
    const limit = Math.max(0, Math.trunc(Number(ctx.query.get('limit')) || 50))
    const offset = Math.max(0, Math.trunc(Number(ctx.query.get('offset')) || 0))
    // 返すページの分だけ突き合わせる。クライアントは全件を数ページに分けて取りに来るので、
    // 毎回全件を突き合わせると同じ照合を（ページ数）回やり直すことになる。
    const items: CorpusItem[] = newestFirst.slice(offset, offset + limit).map((c) => {
      const review = latestReview.get(c.message_id)
      // 'none'（判定の取り消し）は未レビューと同じ扱いにし、ideal も出さない。
      const verdict = review && review.verdict !== 'none' ? review.verdict : null
      return {
        ...c,
        verdict,
        ideal: verdict === null ? null : (review?.ideal ?? null),
        archived: archived.has(c.message_id),
        stale: isStaleReading(c, promptSince),
      }
    })
    const body: CorpusResponse = {
      total: newestFirst.length,
      items,
      prompt_changed_at: promptSince === null ? null : new Date(promptSince).toISOString(),
    }
    sendJson(res, 200, body)
  })

  // review タブの👍/👎と「理想の出力」欄の書き込み先。verdict: 'none' は取り消し
  // （同じ判定をもう一度押したとき）を意味し、reviews.jsonl には行として残る
  // （追記専用。読み取り側が最新行を優先する）。ideal は verdict==='bad' のときだけ
  // 保存し、それ以外はクライアントが何を送っても空文字にする。
  addRoute('POST', '/api/reviews', async (req, res) => {
    const body = await readJsonBody(req)
    if (
      !isValidMessageId(body.message_id) ||
      (body.verdict !== 'good' && body.verdict !== 'bad' && body.verdict !== 'none')
    ) {
      return badRequest(res, 'message_id and verdict ("good"|"bad"|"none") are required')
    }
    if (body.ideal !== undefined && typeof body.ideal !== 'string') {
      return badRequest(res, 'ideal must be a string')
    }
    const record = {
      ts: new Date().toISOString(),
      message_id: body.message_id,
      verdict: body.verdict,
      ideal: body.verdict === 'bad' ? body.ideal || '' : '',
    }
    await appendJsonl(REVIEWS_FILE, [record])
    sendJson(res, 200, { ok: true })
  })

  // review タブの「アーカイブ」/「戻す」ボタン。「戻す」は archived:false の行を
  // 追記するだけで、以前の archived:true 行は消えない（最新行が優先されるだけ）。
  addRoute('POST', '/api/archives', async (req, res) => {
    const body = await readJsonBody(req)
    if (!isValidMessageId(body.message_id) || typeof body.archived !== 'boolean') {
      return badRequest(res, 'message_id (non-empty string) and archived (boolean) are required')
    }
    const record = {
      ts: new Date().toISOString(),
      message_id: body.message_id,
      archived: body.archived,
    }
    await appendJsonl(ARCHIVES_FILE, [record])
    sendJson(res, 200, { ok: true })
  })

  // review タブの一括アーカイブボタン。対象の選定はクライアント側の責任
  // （features/review/reviewCounts.ts の bulkArchiveTargets）。ボタンの活性条件
  // （stale を除外）と実際の対象（stale を問わない）は食い違っているが、以前の実装から
  // 引き継いだ挙動なので、そのままにしている。
  addRoute('POST', '/api/archives/bulk', async (req, res) => {
    const body = await readJsonBody(req)
    const messageIds: unknown[] | null = Array.isArray(body.message_ids) ? body.message_ids : null
    if (
      !messageIds ||
      messageIds.length === 0 ||
      typeof body.archived !== 'boolean' ||
      !messageIds.every(isValidMessageId)
    ) {
      return badRequest(
        res,
        'message_ids (non-empty string array) and archived (boolean) are required',
      )
    }
    const ts = new Date().toISOString()
    await appendJsonl(
      ARCHIVES_FILE,
      messageIds.map((messageId) => ({ ts, message_id: messageId, archived: body.archived })),
    )
    sendJson(res, 200, { ok: true, count: messageIds.length })
  })

  // review タブの再生ボタンが叩く経路。音声が無い（record 自体が無い／audio_path が
  // 無い／ファイルが実在しない）場合はすべて 404 で返す。これは studio のルート未一致の
  // 404 と同じ本文（notFound）なので、クライアント側は lib/api.ts の allowNotFound
  // オプトアウトでこの 404 だけ「サーバーが古い」扱いから外している。
  addRoute('GET', '/api/audio/:message_id', async (req, res, ctx) => {
    const corpus = await readJsonlSafe<CorpusRecord>(CORPUS_FILE)
    const record = corpus.findLast((c) => c.message_id === ctx.params.message_id)
    if (!record || !record.audio_path) return notFound(res)

    // 相対パスの検証（ディレクトリ外への脱出の拒否）は resolveAudioPath 側で行う。
    const resolved = await resolveAudioPath(record.audio_path)
    if (!resolved) return notFound(res)

    await streamWav(res, resolved)
  })
}
