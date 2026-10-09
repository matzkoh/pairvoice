// PCM の wav をつなぐ。pairvoice が書き出す wav（モノラル・16bit・同じサンプリング周波数）を
// 1本の参照音声にまとめるのに使う

type Pcm = { format: Buffer; sampleRate: number; blockAlign: number; data: Buffer }

export class WavFormatError extends Error {}

// RIFF/WAVE の見出しがあるか。中身の妥当性は見ない（mp3 などの取り違えだけを弾く）
export function hasWavHeader(buffer: Buffer) {
  return (
    buffer.length >= 12 &&
    buffer.subarray(0, 4).toString('latin1') === 'RIFF' &&
    buffer.subarray(8, 12).toString('latin1') === 'WAVE'
  )
}

function readPcm(buffer: Buffer): Pcm {
  if (!hasWavHeader(buffer)) throw new WavFormatError('not a wav file')
  let format: Buffer | null = null
  let data: Buffer | null = null
  // チャンクは「名前4バイト、長さ4バイト、本体」が続き、本体の長さが奇数なら1バイト詰める
  for (let at = 12; at + 8 <= buffer.length;) {
    const id = buffer.subarray(at, at + 4).toString('latin1')
    const size = buffer.readUInt32LE(at + 4)
    const body = buffer.subarray(at + 8, Math.min(buffer.length, at + 8 + size))
    if (id === 'fmt ') format = body
    if (id === 'data') data = body
    at += 8 + size + (size % 2)
  }
  if (!format || format.length < 16 || !data) throw new WavFormatError('missing fmt or data')
  if (format.readUInt16LE(0) !== 1) throw new WavFormatError('not linear PCM')
  return {
    format: format.subarray(0, 16),
    sampleRate: format.readUInt32LE(4),
    blockAlign: format.readUInt16LE(12),
    data,
  }
}

// 同じ形式の wav を、gapSeconds の無音を挟んでつなぐ。形式が違えば WavFormatError
export function concatWavs(buffers: readonly Buffer[], gapSeconds: number): Buffer {
  const pieces = buffers.map(readPcm)
  const first = pieces[0]
  if (!first) throw new WavFormatError('no wav')
  if (pieces.some((p) => !p.format.equals(first.format))) {
    throw new WavFormatError('wav formats differ')
  }
  const gap = Buffer.alloc(Math.round(gapSeconds * first.sampleRate) * first.blockAlign)
  const data = Buffer.concat(pieces.flatMap((p, i) => (i === 0 ? [p.data] : [gap, p.data])))
  const header = Buffer.alloc(44)
  header.write('RIFF', 0, 'latin1')
  header.writeUInt32LE(36 + data.length, 4)
  header.write('WAVE', 8, 'latin1')
  header.write('fmt ', 12, 'latin1')
  header.writeUInt32LE(16, 16)
  first.format.copy(header, 20)
  header.write('data', 36, 'latin1')
  header.writeUInt32LE(data.length, 40)
  return Buffer.concat([header, data])
}
