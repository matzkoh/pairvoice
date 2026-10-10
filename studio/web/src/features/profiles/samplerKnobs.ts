import type { SamplerOverrides } from '@/lib/api-types'

// ノブの定義をここ1箇所に集める。フォームはこれを回して描き、toSamplerPayload と
// tomlSnippet も同じ定義から組む。項目を増やすのが1行で済み、描画と送信のどちらかを
// 忘れる余地が無い。
export type SamplerKey = keyof SamplerOverrides

// slider / select / bool は値そのもの、optionalNumber は入力中の生文字列を持つ。
// 生文字列で持つのは「0.」のような途中の状態を数に丸めずに扱うためで、空文字は
// 「未指定（モデル既定に任せる）」を表す。
export type KnobValue = number | string | boolean
export type SamplerValues = Record<SamplerKey, KnobValue>

// hint は本家のパラメータガイドの要約。絵文字の英語名を title に入れているのと同じ
// 理由で、キー名だけでは何が起きるか読めない項目に説明を添える。
// 出典は Aratako/Irodori-TTS の v3 タグの docs/parameters.md（main は v4-Small 向け。
// この17項目の記述自体は両者で一字一句同じだが、使っているモデルに合う版を引く）。
type Common = { key: SamplerKey; label: string; hint: string; group: 'sampling' | 'advanced' }

export type Knob =
  | (Common & { kind: 'slider'; min: number; max: number; step: number; init: number })
  | (Common & { kind: 'select'; options: readonly string[]; init: string })
  | (Common & { kind: 'bool'; init: boolean })
  | (Common & { kind: 'optionalNumber'; placeholder: string })

// 既定値はモデルの config.json に従う（mlx-audio の SamplerConfig のクラス既定と一致）。
// デモの UI は cfg_scale_caption を 4.0 で始めるが、ここは 3.0 にしてある。初期状態を
// 「いまこのモデルとこの設定で鳴っている音」に合わせるほうが、差分として結果を読める。
export const KNOBS: readonly Knob[] = [
  {
    key: 'num_steps',
    label: 'Num Steps',
    hint: 'Euler 積分のステップ数。増やすほど遅くなるが、ある点までは安定する。まず最初に動かす品質と速度のつまみ。仕上げは既定から始める',
    group: 'sampling',
    kind: 'slider',
    min: 1,
    max: 120,
    step: 1,
    init: 40,
  },
  {
    key: 'cfg_guidance_mode',
    label: 'CFG Guidance Mode',
    hint: 'CFG の掛け方。independent は条件ごとに無条件側を持ち、テキスト・caption・話者に別々の強さを与えられる（そのぶん計算量が増える）。joint は全条件をまとめて落とし、強さを揃える前提。alternating は落とす条件をステップごとに入れ替える',
    group: 'sampling',
    kind: 'select',
    options: ['independent', 'joint', 'alternating'],
    init: 'independent',
  },
  {
    key: 'cfg_scale_text',
    label: 'CFG Scale Text',
    hint: 'テキスト条件付けの強さ。上げるほど原文に忠実になる。発音が怪しいときは少しだけ上げる。上げすぎると不自然になる',
    group: 'sampling',
    kind: 'slider',
    min: 0,
    max: 10,
    step: 0.1,
    init: 3.0,
  },
  {
    key: 'cfg_scale_caption',
    label: 'CFG Scale Caption',
    hint: 'caption（声の指示文）条件付けの強さ。上げるほど指示文の声質・話し方に寄る。VoiceDesign 系のモデルで効く',
    group: 'sampling',
    kind: 'slider',
    min: 0,
    max: 10,
    step: 0.1,
    init: 3.0,
  },
  {
    key: 'cfg_scale_speaker',
    label: 'CFG Scale Speaker',
    hint: '参照音声の話者条件付けの強さ。声が参照から離れるときはここか Speaker KV Scale を先に試す',
    group: 'sampling',
    kind: 'slider',
    min: 0,
    max: 10,
    step: 0.1,
    init: 5.0,
  },
  {
    key: 't_schedule_mode',
    label: 'Time Schedule',
    hint: 'RF Euler サンプリングの timestep スケジュール。sway にすると Sway Sampling が有効になり、ステップ数を減らしても持つことがある',
    group: 'sampling',
    kind: 'select',
    options: ['linear', 'sway'],
    init: 'linear',
  },
  {
    key: 'sway_coeff',
    label: 'Sway Coeff',
    hint: 'Sway Sampling の係数。負の値ほどノイズ側にスケジュールの解像度を割く。Time Schedule が sway のときだけ効く',
    group: 'sampling',
    kind: 'slider',
    min: -1,
    max: 1.5,
    step: 0.1,
    init: -1.0,
  },
  {
    key: 'duration_scale',
    label: 'Duration Scale',
    hint: 'モデルが予測した長さに掛ける倍率。1.0 より大きいと長く（ゆっくり）、小さいと短くなる。Seconds を指定したときは効かない',
    group: 'sampling',
    kind: 'slider',
    min: 0.5,
    max: 1.5,
    step: 0.01,
    init: 1.0,
  },
  {
    key: 'seconds',
    label: 'Seconds',
    hint: '出力の長さを秒で固定する。指定すると長さの自動決定を上書きする。このモデルは長さを自分で予測できるので、空欄のままが勧められている',
    group: 'sampling',
    kind: 'optionalNumber',
    placeholder: '自動',
  },
  {
    key: 'rng_seed',
    label: 'Seed',
    hint: 'サンプリングの乱数の種。同じモデル・同じ設定なら同じ音が出る。空欄なら候補ごとに studio が乱数で決め、埋めると seed, seed+1, … と振る',
    group: 'sampling',
    kind: 'optionalNumber',
    placeholder: 'ランダム',
  },
  {
    key: 'cfg_min_t',
    label: 'CFG Min t',
    hint: 'CFG が効く timestep の下限。この値より下では CFG を掛けない',
    group: 'advanced',
    kind: 'slider',
    min: 0,
    max: 1,
    step: 0.01,
    init: 0.5,
  },
  {
    key: 'cfg_max_t',
    label: 'CFG Max t',
    hint: 'CFG が効く timestep の上限。この値より上では CFG を掛けない',
    group: 'advanced',
    kind: 'slider',
    min: 0,
    max: 1,
    step: 0.01,
    init: 1.0,
  },
  {
    key: 'context_kv_cache',
    label: 'Context KV Cache',
    hint: 'テキスト・話者・caption の K/V を先に計算して使い回し、サンプリングを速くする。音は変わらない',
    group: 'advanced',
    kind: 'bool',
    init: true,
  },
  {
    key: 'speaker_kv_scale',
    label: 'Speaker KV Scale',
    hint: '話者コンテキストの K/V 射影に掛ける追加スケール。1.0 より大きくすると話者性が強まる。実験的な調整で、参照から声が離れるときに CFG を大きく動かす前に試す',
    group: 'advanced',
    kind: 'optionalNumber',
    placeholder: 'モデル既定',
  },
  {
    key: 'truncation_factor',
    label: 'Truncation Factor',
    hint: 'サンプリング前の初期ガウスノイズに掛ける倍率。0.8〜0.9 にするとばらつきが減るが、表現の幅も落ちうる',
    group: 'advanced',
    kind: 'optionalNumber',
    placeholder: 'モデル既定',
  },
  {
    key: 'rescale_k',
    label: 'Rescale k',
    hint: '時間方向のスコア再スケールの係数。Rescale sigma と対で、両方入れるか両方空欄にする',
    group: 'advanced',
    kind: 'optionalNumber',
    placeholder: 'モデル既定',
  },
  {
    key: 'rescale_sigma',
    label: 'Rescale sigma',
    hint: '時間方向のスコア再スケールの幅。Rescale k と対で、両方入れるか両方空欄にする',
    group: 'advanced',
    kind: 'optionalNumber',
    placeholder: 'モデル既定',
  },
]

// 候補は1件ずつ直列に回るので、デモの32は待ち時間が現実的でない。
export const MAX_CANDIDATES = 8

const DEFAULT_ENTRIES = KNOBS.map((knob) => [
  knob.key,
  knob.kind === 'optionalNumber' ? '' : knob.init,
])
// KNOBS が SamplerKey を網羅することは型では強制できず、samplerKnobs.test.ts が固定している。
// oxlint-disable-next-line typescript/no-unsafe-type-assertion
export const MODEL_DEFAULTS = Object.fromEntries(DEFAULT_ENTRIES) as SamplerValues

// /health の sampler は「pairvoice がいま渡している値」なので、モデル既定の上に重ねる。
// pairvoice が停止していれば undefined で来て、モデル既定のままになる。
export function initialValues(sampler?: SamplerOverrides): SamplerValues {
  const values = { ...MODEL_DEFAULTS }
  if (!sampler) return values
  for (const knob of KNOBS) {
    const value = sampler[knob.key]
    if (value === undefined) continue
    values[knob.key] = knob.kind === 'optionalNumber' ? String(value) : value
  }
  return values
}

// 空欄可のノブの読み取り。「空欄（モデル既定に任せる）」と「数値として読めない」を
// 呼び出し側が区別できる形で返す。判定を1箇所に置かないと、送信ペイロードと TOML
// スニペットで「何を有効な数値とみなすか」が静かにずれる。
export function parseOptionalNumber(value: KnobValue): number | 'blank' | 'invalid' {
  const raw = String(value).trim()
  if (raw === '') return 'blank'
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : 'invalid'
}

export function toSamplerPayload(values: SamplerValues): {
  sampler: SamplerOverrides
  errors: string[]
} {
  const sampler: SamplerOverrides = {}
  const errors: string[] = []
  // どのキーがどの型かは KNOBS が唯一の正本なので、ここではキーごとの型分岐を持たない
  // （持つと同じ対応表を2箇所で保つことになる）。書き込みだけ広い型を通す
  const write = (key: SamplerKey, value: KnobValue) => {
    ;(sampler as Record<string, KnobValue>)[key] = value
  }
  for (const knob of KNOBS) {
    const value = values[knob.key]
    if (knob.kind !== 'optionalNumber') {
      // 空欄を取れない項目は常に送る。「触っていないから既定に落ちる」という
      // 暗黙の経路を作らず、UI に出ている値と効く値を一致させる
      write(knob.key, value)
      continue
    }
    const parsed = parseOptionalNumber(value)
    if (parsed === 'blank') continue
    if (parsed === 'invalid') {
      errors.push(`${knob.key} は数値か空欄にしてください`)
      continue
    }
    write(knob.key, parsed)
  }
  return { sampler, errors }
}
