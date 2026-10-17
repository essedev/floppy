/*
  The four states every view handles: loading (placeholder rows, not a spinner), error with
  nothing to show (a panel with retry), empty, and data. An error after data was loaded is not
  an error screen: the data stays, with a stale notice on top.
*/

import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { fmt, useI18n } from '@/i18n'
import { useFormat } from '@/lib/format'
import { Button } from './Button'
import { useErrorText } from './error-text'
import { Notice, State } from './detail'

/** The title row of a page, with its actions on the right. */
export function PageHeader({ title, lead, actions }: { title: ReactNode; lead?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-[22px] flex flex-wrap items-end justify-between gap-x-5 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-[28px] leading-[1.15] font-bold tracking-[-0.025em]">{title}</h1>
        {lead && <p className="mt-1.5 text-sm text-ink-2">{lead}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </header>
  )
}

/** Nothing to show because the load failed: what happened and a retry. */
export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry(): void }) {
  const { t } = useI18n()
  const text = useErrorText()
  return (
    <div className="notice grid justify-items-start gap-3 px-5 py-5" role="alert">
      <State led="err">{t.states.errorTitle}</State>
      <p className="max-w-[60ch] text-sm text-ink-2">
        {text(error)} {t.states.errorBody}
      </p>
      <Button size="sm" variant="raised" onClick={onRetry}>
        {t.common.retry}
      </Button>
    </div>
  )
}

/** The data on screen is from an earlier load: when, and a retry. */
export function StaleNotice({ updatedAt, onRetry }: { updatedAt: number; onRetry(): void }) {
  const { t } = useI18n()
  const f = useFormat()
  return (
    <Notice
      className="mb-3.5"
      state={<State led="err">{t.states.staleTitle}</State>}
      text={fmt(t.states.staleBody, { time: f.relative(new Date(updatedAt).toISOString()) })}
      action={
        <Button size="sm" variant="raised" onClick={onRetry}>
          {t.common.retry}
        </Button>
      }
    />
  )
}

/** A view with nothing in it yet: a picture, what it is, what to do. */
export function EmptyState({ art, title, body, action }: { art?: ReactNode; title: ReactNode; body: ReactNode; action?: ReactNode }) {
  return (
    <div className="grid justify-items-center gap-3 px-5 py-16 text-center">
      {art}
      <h2 className="text-xl font-bold">{title}</h2>
      <p className="max-w-[44ch] text-[14.5px] text-ink-2">{body}</p>
      {action}
    </div>
  )
}

/** Placeholder rows of a table while it loads. */
export function SkeletonRows({ rows = 5, widths }: { rows?: number; widths: number[] }) {
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r} aria-hidden>
          {widths.map((w, c) => (
            <td key={c}>
              <span className="sk" style={{ width: w }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

/** Placeholder lines for a panel while it loads. */
export function SkeletonLines({ lines = 3 }: { lines?: number }) {
  return (
    <div className="grid gap-3" aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="sk" style={{ width: `${80 - i * 15}%` }} />
      ))}
    </div>
  )
}

/**
 * Renders a query in its four states. `loading` shows while nothing is there; `children`
 * gets the data, with a stale notice above it when a refetch failed.
 */
export function QueryView<T>({ query, loading, children, isEmpty, empty, staleNotice = true }: {
  query: UseQueryResult<T>
  loading: ReactNode
  children(data: T): ReactNode
  isEmpty?(data: T): boolean
  empty?: ReactNode
  staleNotice?: boolean
}) {
  const { t } = useI18n()
  if (query.data === undefined) {
    if (query.isError) return <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />
    return (
      <div aria-busy="true" aria-label={t.common.loading}>
        {loading}
      </div>
    )
  }
  const data = query.data
  return (
    <>
      {staleNotice && query.isError && <StaleNotice updatedAt={query.dataUpdatedAt} onRetry={() => void query.refetch()} />}
      {isEmpty?.(data) && empty ? empty : children(data)}
    </>
  )
}
