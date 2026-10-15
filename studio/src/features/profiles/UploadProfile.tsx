import { useQueryClient } from '@tanstack/react-query'
import { useState, useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { apiUpload, toErrorMessage } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'

import { Field } from './Field'
import { FilePicker } from './FilePicker'
import { invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// 手持ちの wav（数秒〜十数秒）をそのまま参照音声にする。caption はその声を
// どう喋らせるかの指示として合成のたびに併せて渡る
export function UploadProfile({ onStatus, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [caption, setCaption] = useState('')
  const [pending, startTransition] = useTransition()
  // 保存後に選択を空にするには input を作り直すしかない（value を書き換えられない）
  const [inputKey, setInputKey] = useState(0)
  const canSave = file !== null && name.trim() !== '' && !pending

  function save() {
    if (!file || !canSave) return
    const query = new URLSearchParams({ name: name.trim(), caption: caption.trim() })
    startTransition(async () => {
      try {
        const created = await apiUpload<ProfileItem>(`/profiles/upload?${query}`, file)
        await invalidateProfiles(queryClient)
        onStatus({ message: `「${created.name}」を取り込みました`, isError: false })
        setFile(null)
        setName('')
        setCaption('')
        setInputKey((k) => k + 1)
        onCreated(created.id)
      } catch (err: unknown) {
        onStatus({ message: `取り込みに失敗しました: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  return (
    <div className="max-w-xl space-y-5">
      <Field label="参照音声" hint="声そのもの。数秒〜十数秒の wav を、この声で読み上げます。">
        <FilePicker
          label="wav を選ぶ"
          accept=".wav,audio/wav"
          resetKey={inputKey}
          summary={file ? file.name : '選ばれていません'}
          onPick={([picked]) => {
            setFile(picked ?? null)
            if (picked && name === '') setName(picked.name.replace(/\.wav$/i, ''))
          }}
        />
      </Field>
      <Field
        label="caption"
        htmlFor="upload-caption"
        hint="話し方の指示。合成のたびに参照音声と併せて渡ります。空なら参照音声だけで読みます。"
      >
        <Textarea
          id="upload-caption"
          rows={2}
          placeholder="例: 落ち着いた低い声。ゆっくりと丁寧に話す。"
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
        />
      </Field>
      <Field label="名前" htmlFor="upload-name">
        <Input id="upload-name" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Button disabled={!canSave} onClick={save}>
        プロファイルを作る
      </Button>
    </div>
  )
}
