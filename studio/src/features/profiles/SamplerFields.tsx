import { Info } from 'lucide-react'

import { Input } from '@/components/ui/input'

import {
  KNOBS,
  type Knob,
  type KnobValue,
  type SamplerKey,
  type SamplerValues,
} from './samplerKnobs'

type Props = {
  values: SamplerValues
  onChange: (key: SamplerKey, value: KnobValue) => void
  disabled: boolean
}

function KnobField({ knob, values, onChange, disabled }: { knob: Knob } & Props) {
  const id = `knob-${knob.key}`
  const value = values[knob.key]
  // sway_coeff は t_schedule_mode が sway のときだけ効く
  const inert = disabled || (knob.key === 'sway_coeff' && values.t_schedule_mode !== 'sway')

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {/* キー名だけでは何が起きるか読めないので、パラメータガイドの要約を title に入れる */}
        <label htmlFor={id} title={knob.hint}>
          {knob.label}
        </label>
        <span title={knob.hint} aria-hidden="true">
          <Info className="size-3 opacity-60" />
        </span>
        {knob.kind === 'slider' && (
          <span className="ml-auto font-mono text-foreground tabular-nums">{String(value)}</span>
        )}
      </div>
      {knob.kind === 'slider' && (
        <input
          id={id}
          type="range"
          min={knob.min}
          max={knob.max}
          step={knob.step}
          value={Number(value)}
          disabled={inert}
          onChange={(e) => onChange(knob.key, Number(e.target.value))}
          className="w-full accent-primary"
        />
      )}
      {knob.kind === 'select' && (
        <select
          id={id}
          value={String(value)}
          disabled={inert}
          onChange={(e) => onChange(knob.key, e.target.value)}
          className="h-8 w-full rounded-md border bg-transparent px-2 text-sm focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
        >
          {knob.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      )}
      {knob.kind === 'bool' && (
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          disabled={inert}
          onChange={(e) => onChange(knob.key, e.target.checked)}
          className="size-4 accent-primary"
        />
      )}
      {knob.kind === 'optionalNumber' && (
        <Input
          id={id}
          type="text"
          inputMode="decimal"
          placeholder={knob.placeholder}
          value={String(value)}
          disabled={inert}
          onChange={(e) => onChange(knob.key, e.target.value)}
          className="h-8"
        />
      )}
    </div>
  )
}

export function SamplerFields(props: Props) {
  const sampling = KNOBS.filter((knob) => knob.group === 'sampling')
  const advanced = KNOBS.filter((knob) => knob.group === 'advanced')
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        {sampling.map((knob) => (
          <KnobField key={knob.key} knob={knob} {...props} />
        ))}
      </div>
      {/* 効き方が分かりにくい項目は畳んでおく。既定のままで困らない */}
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">Advanced</summary>
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3">
          {advanced.map((knob) => (
            <KnobField key={knob.key} knob={knob} {...props} />
          ))}
        </div>
      </details>
    </div>
  )
}
