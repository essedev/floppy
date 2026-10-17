/*
  The control for one person's access to one disk (DECISIONS #23): a menu with none, read,
  read and write. A change not yet on the person's devices (`applied: false`) shows as a lit
  LED next to it, the only colour; the text says the same for screen readers.
*/

import { ChevronDown } from 'lucide-react'
import { Led, Readout } from '@/components/detail'
import { Menu } from '@/components/Menu'
import { useErrorText } from '@/components/error-text'
import { fmt, useI18n } from '@/i18n'
import type { AccessLevel, DiskAccess } from '@/lib/api'
import { ACCESS_LEVELS, useSetAccess } from './hooks'


/** A menu button for an access level, for a form or a live cell. */
export function AccessPicker({ value, onChange, label, busy = false, disabled = false, align = 'left' }: {
  value: AccessLevel
  onChange(level: AccessLevel): void
  /** What it sets, for screen readers: "Accesso di Marco a Progetti". */
  label: string
  busy?: boolean
  disabled?: boolean
  align?: 'left' | 'right'
}) {
  const { t } = useI18n()
  return (
    <Menu
      align={align}
      items={ACCESS_LEVELS.map((level) => ({ label: t.access.levels[level], checked: value === level, onSelect: () => level !== value && onChange(level) }))}
      trigger={(props) => (
        <button
          type="button"
          {...props}
          disabled={disabled}
          aria-label={`${label}: ${t.access.levels[value]}`}
          aria-busy={busy || undefined}
          className={`inline-flex h-[30px] items-center gap-1 rounded-lg border-0 bg-transparent px-2 text-sm whitespace-nowrap hover:bg-sunken disabled:opacity-55 ${value === 'none' ? 'text-ink-3' : 'text-ink'}`}
        >
          {t.access.levels[value]}
          <ChevronDown size={15} aria-hidden />
        </button>
      )}
    />
  )
}

/** One row of an AccessList: a disk or a person. */
export interface AccessRowItem {
  id: string
  label: string
  sub?: string
}

/**
 * The access a form will send, one picker per row, with an "Everyone" row on top that sets
 * them all: the common case of a disk for the whole team costs one choice (DECISIONS #23).
 */
export function AccessList({ rows, value, onChange, labelFor }: {
  rows: AccessRowItem[]
  value: Record<string, AccessLevel>
  onChange(next: Record<string, AccessLevel>): void
  /** The picker's accessible name for a row. */
  labelFor(row: AccessRowItem): string
}) {
  const { t } = useI18n()
  const levels = new Set(rows.map((r) => value[r.id] ?? 'none'))
  const all = levels.size === 1 ? [...levels][0] : undefined
  return (
    <ul className="m-0 grid max-h-[248px] list-none overflow-y-auto rounded-xl p-0 text-sm shadow-[inset_0_0_0_1px_var(--line)]">
      {rows.length > 1 && (
        <li className="flex items-center justify-between gap-3 px-3.5 py-1.5">
          <b className="font-semibold text-ink">{t.access.everyone}</b>
          <Menu
            align="right"
            items={ACCESS_LEVELS.map((level) => ({ label: t.access.levels[level], checked: all === level, onSelect: () => onChange(Object.fromEntries(rows.map((r) => [r.id, level]))) }))}
            trigger={(props) => (
              <button type="button" {...props} aria-label={t.access.everyone} className="inline-flex h-[30px] items-center gap-1 rounded-lg border-0 bg-transparent px-2 text-sm whitespace-nowrap text-ink hover:bg-sunken">
                {all ? t.access.levels[all] : t.access.mixed}
                <ChevronDown size={15} aria-hidden />
              </button>
            )}
          />
        </li>
      )}
      {rows.map((row) => (
        <li key={row.id} className="flex items-center justify-between gap-3 border-t border-line px-3.5 py-1.5 first:border-t-0">
          <span className="min-w-0">
            <span className="block truncate text-ink">{row.label}</span>
            {row.sub && <span className="block truncate text-xs text-ink-3">{row.sub}</span>}
          </span>
          <AccessPicker value={value[row.id] ?? 'none'} align="right" label={labelFor(row)} onChange={(level) => onChange({ ...value, [row.id]: level })} />
        </li>
      ))}
    </ul>
  )
}

/** The LED of a change still owed to someone's devices; blinks while the request runs. */
export function PendingLed({ pending, busy }: { pending: boolean; busy: boolean }) {
  const { t } = useI18n()
  if (!pending && !busy) return <span className="inline-block size-[14px]" aria-hidden />
  return <Led state="dl" blink={busy} label={t.access.pending} />
}

/** A live access: the picker, saved on change, and the owed state. */
export function AccessCell({ entry, person, disk, onError, align, withText = false }: {
  entry: DiskAccess
  person: string
  disk: string
  onError(text: string): void
  align?: 'left' | 'right'
  /** Says "waiting" next to the LED, where a list has room (the matrix has a legend instead). */
  withText?: boolean
}) {
  const { t } = useI18n()
  const errorText = useErrorText()
  const set = useSetAccess()
  const value = set.isPending ? (set.variables?.access ?? entry.access) : entry.access
  return (
    <span className="inline-flex items-center gap-1.5">
      <AccessPicker
        value={value}
        busy={set.isPending}
        align={align}
        label={fmt(t.access.change, { person, disk })}
        onChange={(access) =>
          set.mutate({ diskId: entry.diskId, personId: entry.personId, access }, { onError: (error) => onError(errorText(error)) })
        }
      />
      {withText && !entry.applied && !set.isPending && <Readout className="order-first">{t.access.pendingReadout}</Readout>}
      <PendingLed pending={!entry.applied} busy={set.isPending} />
    </span>
  )
}
