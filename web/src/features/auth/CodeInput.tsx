/* Six boxes for the 6-digit code: typing moves forward, Backspace back, pasting fills all. */

import { useRef } from 'react'
import { fmt, useI18n } from '@/i18n'

/** The code boxes; `onComplete` fires when the sixth digit is in. */
export function CodeInput({ value, onChange, onComplete, disabled, invalid, labelledBy }: {
  value: string
  onChange(value: string): void
  onComplete(code: string): void
  disabled?: boolean
  invalid?: boolean
  labelledBy: string
}) {
  const { t } = useI18n()
  const refs = useRef<(HTMLInputElement | null)[]>([])
  const digits = Array.from({ length: 6 }, (_, i) => value[i] ?? '')

  const set = (next: string) => {
    const clean = next.replace(/\D/g, '').slice(0, 6)
    onChange(clean)
    if (clean.length === 6) onComplete(clean)
    return clean
  }

  return (
    <div role="group" aria-labelledby={labelledBy} className="flex items-center gap-2">
      {digits.map((digit, i) => (
        <span key={i} className="contents">
          {i === 3 && <span className="w-1.5" aria-hidden />}
          <input
            ref={(el) => {
              refs.current[i] = el
            }}
            inputMode="numeric"
            autoComplete={i === 0 ? 'one-time-code' : 'off'}
            maxLength={6}
            aria-label={fmt(t.auth.digit, { n: i + 1 })}
            aria-invalid={invalid || undefined}
            disabled={disabled}
            value={digit}
            onFocus={(e) => e.currentTarget.select()}
            onChange={(e) => {
              const typed = e.currentTarget.value.replace(/\D/g, '')
              if (!typed) return
              // A paste or an autofill lands in one box: spread it from here.
              const next = set(value.slice(0, i) + typed + value.slice(i + typed.length))
              refs.current[Math.min(next.length, 5)]?.focus()
            }}
            onKeyDown={(e) => {
              if (e.key === 'Backspace') {
                e.preventDefault()
                const at = digit ? i : Math.max(0, i - 1)
                set(value.slice(0, at) + value.slice(at + 1))
                refs.current[at]?.focus()
              }
              if (e.key === 'ArrowLeft') refs.current[i - 1]?.focus()
              if (e.key === 'ArrowRight') refs.current[i + 1]?.focus()
            }}
            className="num h-14 w-11 rounded-[10px] border-0 bg-raised text-center text-[26px] shadow-[inset_0_0_0_1.5px_var(--line)] outline-none focus:shadow-[inset_0_0_0_2px_var(--ink)] aria-invalid:shadow-[inset_0_0_0_2px_var(--bad-text)] disabled:opacity-60"
          />
        </span>
      ))}
    </div>
  )
}
