/* Form controls: a labelled field, text input, option cards, checkbox. */

import { useId, type InputHTMLAttributes, type ReactNode } from 'react'

/** A label, a control, a hint or an error under it. The control gets the id it needs. */
export function Field({ label, hint, error, children }: { label: ReactNode; hint?: ReactNode; error?: string | null; children: (id: string, describedBy: string | undefined) => ReactNode }) {
  const id = useId()
  const noteId = `${id}-note`
  return (
    <div className="grid content-start gap-1.5">
      <label htmlFor={id} className="text-[13px] font-semibold">
        {label}
      </label>
      {children(id, hint || error ? noteId : undefined)}
      {error ? (
        <span id={noteId} className="text-[13px] text-bad-text" role="alert">
          {error}
        </span>
      ) : (
        hint && (
          <span id={noteId} className="text-[13px] text-ink-2">
            {hint}
          </span>
        )
      )}
    </div>
  )
}

/** A text input; `mono` sets it in the readout face for keys, hosts and buckets. */
export function Input({ mono = false, invalid = false, className = '', ...rest }: InputHTMLAttributes<HTMLInputElement> & { mono?: boolean; invalid?: boolean }) {
  return (
    <input
      aria-invalid={invalid || undefined}
      className={`h-[42px] w-full rounded-[11px] border-0 bg-raised px-3.5 shadow-[inset_0_0_0_1px_var(--line)] outline-none placeholder:text-ink-3 focus:shadow-[inset_0_0_0_2px_var(--ink)] aria-invalid:shadow-[inset_0_0_0_2px_var(--bad-text)] disabled:opacity-60 ${mono ? 'num text-[13.5px]' : 'text-[14.5px]'} ${className}`}
      {...rest}
    />
  )
}

/** One option of a Choice. */
export interface ChoiceOption<T extends string> {
  value: T
  title: ReactNode
  hint: ReactNode
  aside?: ReactNode
}

/** Two or more options as cards; the chosen one carries an ink outline. */
export function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: ChoiceOption<T>[]; onChange(value: T): void }) {
  return (
    <div role="radiogroup" aria-label={label} className="grid gap-2.5 sm:grid-cols-2">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className="grid gap-0.5 rounded-xl border-0 bg-raised px-3.5 py-3 text-left shadow-[inset_0_0_0_1px_var(--line)] aria-checked:shadow-[inset_0_0_0_2px_var(--ink)]"
        >
          <b className="text-sm">{o.title}</b>
          <span className="text-[13px] text-ink-2">{o.hint}</span>
          {o.aside && <span className="mt-1.5">{o.aside}</span>}
        </button>
      ))}
    </div>
  )
}

/** A checkbox with its label on the right. */
export function Checkbox({ label, hint, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; hint?: ReactNode }) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5 text-sm">
      <input type="checkbox" className="mt-0.5 size-[18px] shrink-0 accent-[var(--ink)]" {...rest} />
      <span className="grid gap-0.5">
        <span>{label}</span>
        {hint && <span className="text-[13px] text-ink-2">{hint}</span>}
      </span>
    </label>
  )
}
