// pairvoice の API を叩く唯一の入口。画面も同じ pairvoice が配るので、同じオリジンで呼ぶ。
// 失敗を必ず例外にし、本文を捨てない。画面はこの例外を見てエラーを出す。

// API は /api の下にあり、画面は根に置かれる。呼び出し側は /api を付けずに道筋を渡す
export function apiUrl(path: string): string {
  return `/api${path}`
}

export class ApiError extends Error {
  readonly status: number
  readonly body: string

  constructor(status: number, body: string, message?: string) {
    super(message ?? `${status}: ${body}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

// 404 は「そのルートがまだ無い」の意味になる。画面のタブを開いたまま pairvoice を
// 古い版に戻すと起きる。原因の説明を例外の側に持たせて、画面が黙って固まらないようにする。
export class StaleServerError extends ApiError {
  constructor(body: string) {
    super(
      404,
      body,
      'pairvoice が古い可能性があります。更新して `pairvoice restart` で再起動してください。',
    )
    this.name = 'StaleServerError'
  }
}

// fetch 自体が reject するのは pairvoice が止まっている（再起動中を含む）
// 場合で、応答が返ってくる 404（StaleServerError）とは別の障害モード。ブラウザが投げる
// 'Failed to fetch' をそのまま画面に出しても打ち手が読み取れないので、ここで置き換える。
// HTTP 応答が無い状況なので status は持たせない（嘘の数字を入れない）。そのため
// ApiError は継承せず、元の例外は cause に残す。
export class UnreachableServerError extends Error {
  readonly path: string

  constructor(path: string, cause: unknown) {
    super(
      'pairvoice に接続できませんでした。再起動中なら少し待ち、止まっていれば `pairvoice restart` で起動してください。',
      {
        cause,
      },
    )
    this.name = 'UnreachableServerError'
    this.path = path
  }
}

// catch (err: unknown) から人間向けの文字列を取り出す決まり文句。ApiError /
// UnreachableServerError はどちらも Error のサブクラスなので message で拾える。
// Error でない例外（reject(文字列) 等）は String() にフォールバックする。
// 同一の三項演算子が15ファイル17箇所に散っていたため、ここに一本化する。
export function toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

// pairvoice の /synthesize はバリデーションエラー等で SpeakResponse に無い形（detail/error）を
// 返すことがある。型だけではこの形を保証できないので、実際の値を見て読み分ける
export function speakErrorMessage(result: unknown): string {
  if (isRecord(result)) {
    if (typeof result.detail === 'string') return result.detail
    if (typeof result.error === 'string') return result.error
  }
  return '音声が返りませんでした'
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(apiUrl(path), init)
  } catch (cause) {
    // 呼び出し側が打ち切ったのは、サーバーに届かなかったのとは別の出来事
    if (init?.signal?.aborted) throw cause
    throw new UnreachableServerError(path, cause)
  }
}

export type ApiOptions = {
  // 既定は false: 404 は「そのルートがまだ無い」＝サーバーが古い、として
  // StaleServerError にする。音声ファイル不在（GET /corpus/:id/audio）のように、
  // 404 が正当な業務上の答えになるエンドポイントだけ true にする。
  //
  // 判定は応答の本文ではなく「そのエンドポイントに 404 が起こり得ることを知っている」
  // 呼び出し側に持たせる。本文の形に頼ると、pairvoice のエラーの形を変えたときに
  // 「サーバーが古い」へ化ける。
  allowNotFound?: boolean
  // 画面を離れた・次へ進んだときに、待っている要求を打ち切る
  signal?: AbortSignal
}

// pairvoice は失敗を { error, detail? } の形で返す（FastAPI の 422 は detail が配列）。
// message・detail が文字列なら人間向けの説明なのでそれを使い、
// 無ければ error のコード（例: 'profile_in_use'）を使う。JSON として読めない
// 応答（プロキシのエラーページ等）は今までどおり `${path}: ${status} ${body}` に
// フォールバックする。body をそのまま message にすると、インライン表示（各画面）に生 JSON が
// 出てしまうため、ここ1箇所で直す。
function describeError(path: string, status: number, body: string): string {
  try {
    const parsed: unknown = JSON.parse(body)
    if (parsed && typeof parsed === 'object') {
      const { message, detail, error } = parsed as {
        message?: unknown
        detail?: unknown
        error?: unknown
      }
      if (typeof message === 'string' && message) return message
      if (typeof detail === 'string' && detail) return detail
      if (typeof error === 'string' && error) return error
    }
  } catch {
    // JSON として読めない応答
  }
  return `${path}: ${status} ${body}`
}

// 404 以外の失敗判定と本文の取り出しを一箇所にまとめる。apiGet と apiGetBlob の
// どちらも「ステータスを見て例外にする」部分は同じで、違うのは成功時の中身の読み方
// （JSON/text か blob か）だけ。
async function ensureOk(res: Response, path: string, opts?: ApiOptions): Promise<void> {
  if (res.ok) return
  const body = await res.text()
  if (res.status === 404 && !opts?.allowNotFound) throw new StaleServerError(body)
  throw new ApiError(res.status, body, describeError(path, res.status, body))
}

async function unwrap<T>(res: Response, path: string, opts?: ApiOptions): Promise<T> {
  await ensureOk(res, path, opts)
  const contentType = res.headers.get('content-type') ?? ''
  // JSON でない応答（音声ファイル名など）もあるので、型で嘘をつかないよう
  // Content-Type で分ける。呼び出し側が T を指定する責任を持つ。
  const data: unknown = contentType.includes('application/json')
    ? await res.json()
    : await res.text()
  // T はサーバーの契約（shared/api-types.ts）が決める。全エンドポイントの検査は持たない。
  // oxlint-disable-next-line typescript/no-unsafe-type-assertion
  return data as T
}

export async function apiGet<T>(path: string, opts?: ApiOptions): Promise<T> {
  return unwrap<T>(await send(path), path, opts)
}

export async function apiSend<T>(
  path: string,
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  body?: unknown,
  opts?: ApiOptions,
): Promise<T> {
  const init: RequestInit = {
    method,
    signal: opts?.signal,
    ...(body !== undefined && {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  }
  return unwrap<T>(await send(path, init), path, opts)
}

// 音声など JSON でも文字列でもない応答を扱う経路（レビュー画面の再生ボタン）専用。
// unwrap の text/JSON 分岐を通すと音声バイト列が壊れるため、成功時は blob() で返す。
export async function apiGetBlob(path: string, opts?: ApiOptions): Promise<Blob> {
  const res = await send(path)
  await ensureOk(res, path, opts)
  return res.blob()
}

// 音声ファイルの取り込み（POST /profiles/upload）専用。apiSend のように JSON にすると
// バイト列が壊れるので、ファイルの型のまま本体として送る。
export async function apiUpload<T>(path: string, file: Blob, opts?: ApiOptions): Promise<T> {
  const res = await send(path, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  })
  return unwrap<T>(res, path, opts)
}
