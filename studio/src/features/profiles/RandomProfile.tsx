import { useQueryClient } from '@tanstack/react-query'
import { useState, useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { apiSend, toErrorMessage } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'

import { Field } from './Field'
import { MemberPicker, MIN_MEMBERS } from './MemberPicker'
import { invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  voices: readonly ProfileItem[]
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// 参照音声を持たず、読み上げのたびにメンバーから1つを選ぶ声。口調もその声のものを使う
export function RandomProfile({ voices, onStatus, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [members, setMembers] = useState<string[]>([])
  const [pending, startTransition] = useTransition()
  const canSave = name.trim() !== '' && members.length >= MIN_MEMBERS && !pending

  function save() {
    if (!canSave) return
    startTransition(async () => {
      try {
        const created = await apiSend<ProfileItem>('/profiles/random', 'POST', {
          name: name.trim(),
          members,
        })
        await invalidateProfiles(queryClient)
        onStatus({ message: `「${created.name}」を作りました`, isError: false })
        setName('')
        setMembers([])
        onCreated(created.id)
      } catch (err: unknown) {
        onStatus({ message: `作れませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  return (
    <div className="max-w-xl space-y-5">
      <Field
        label="メンバー"
        hint={`読み上げのたびに、選んだ声から1つを選びます（${MIN_MEMBERS}つ以上）。caption と口調はその声のものを使います。`}
      >
        {voices.length < MIN_MEMBERS ? (
          <p className="text-sm text-muted-foreground">先に声を{MIN_MEMBERS}つ以上作ります</p>
        ) : (
          <MemberPicker voices={voices} value={members} disabled={pending} onChange={setMembers} />
        )}
      </Field>
      <Field label="名前" htmlFor="random-name">
        <Input id="random-name" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Button disabled={!canSave} onClick={save}>
        プロファイルを作る
      </Button>
    </div>
  )
}
