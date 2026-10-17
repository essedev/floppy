/* Buttons of the admin web: ink for the main action, soft, ghost, danger for destructive ones. */

import type { ButtonHTMLAttributes } from 'react'

const VARIANTS = {
  ink: 'bg-ink text-surface hover:opacity-90',
  soft: 'bg-sunken text-ink hover:bg-line',
  // On a sunken surface (notices), where soft would vanish.
  raised: 'bg-raised text-ink hover:bg-line',
  ghost: 'bg-transparent text-ink-2 font-medium hover:text-ink hover:bg-sunken',
  danger: 'bg-bad-text text-raised hover:opacity-90',
}

const SIZES = {
  md: 'h-10 px-4 rounded-[11px] text-sm',
  sm: 'h-[34px] px-3 rounded-[9px] text-[13px]',
}

/** Props of Button: a variant, a size, and a busy flag that disables it. */
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof VARIANTS
  size?: keyof typeof SIZES
  busy?: boolean
}

/** A button drawn on the identity's tokens. */
export function Button({ variant = 'soft', size = 'md', busy = false, className = '', disabled, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex shrink-0 items-center justify-center gap-2 border-0 font-semibold whitespace-nowrap transition-[opacity,background-color] disabled:cursor-default disabled:opacity-55 ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...rest}
    />
  )
}

/** A square icon-only button; the label is required for screen readers. */
export function IconButton({ label, className = '', ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`grid size-[34px] place-items-center rounded-[9px] border-0 bg-transparent text-ink-2 hover:bg-sunken hover:text-ink disabled:opacity-40 ${className}`}
      {...rest}
    />
  )
}
