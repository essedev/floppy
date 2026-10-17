/*
  The audit log (concept-e-admin, "Registro"): who did what, filtered by kind, text and
  period, loaded a page at a time. Loading shows placeholder rows, not a spinner.
*/

import { ChevronDown, Search } from 'lucide-react'
import { useDeferredValue, useState } from 'react'
import { Button } from '@/components/Button'
import { Input } from '@/components/Field'
import { Menu } from '@/components/Menu'
import { useErrorText } from '@/components/error-text'
import { EmptyState, ErrorPanel, PageHeader, SkeletonRows, StaleNotice } from '@/components/states'
import { fmt, useI18n } from '@/i18n'
import type { AuditCategory, AuditQuery } from '@/lib/api'
import { useFormat } from '@/lib/format'
import { useDescribe } from './describe'
import { useAudit } from './hooks'

const CATEGORIES: (AuditCategory | 'all')[] = ['all', 'access', 'people', 'macs', 'key', 'disks', 'settings']
const PERIODS: AuditQuery['sinceDays'][] = [7, 30, 90]
const WIDTHS = [90, 110, 90, 220]

/** The audit log page. */
export function AuditPage() {
  const { t } = useI18n()
  const f = useFormat()
  const describe = useDescribe()
  const errorText = useErrorText()
  const [category, setCategory] = useState<AuditCategory | 'all'>('all')
  const [search, setSearch] = useState('')
  const [sinceDays, setSinceDays] = useState<AuditQuery['sinceDays']>(30)
  const q = useDeferredValue(search.trim())
  const audit = useAudit({ category: category === 'all' ? undefined : category, q: q || undefined, sinceDays, limit: 20 })
  const items = audit.data?.pages.flatMap((p) => p.items) ?? []
  const filtered = category !== 'all' || q !== '' || sinceDays !== 30
  const clear = () => {
    setCategory('all')
    setSearch('')
    setSinceDays(30)
  }

  return (
    <>
      <PageHeader title={t.audit.title} lead={t.audit.lead} />
      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <div role="group" aria-label={t.audit.filters} className="flex flex-wrap gap-1 rounded-xl bg-sunken p-1">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              type="button"
              aria-pressed={category === c}
              onClick={() => setCategory(c)}
              className="h-8 rounded-[9px] border-0 bg-transparent px-3 text-[13px] text-ink-2 aria-pressed:bg-raised aria-pressed:font-semibold aria-pressed:text-ink"
            >
              {t.audit[c]}
            </button>
          ))}
        </div>
        <label className="relative">
          <span className="sr-only">{t.audit.search}</span>
          <Search size={16} className="pointer-events-none absolute top-[13px] left-3 text-ink-3" aria-hidden />
          <Input type="search" className="!w-[240px] pl-9" placeholder={t.audit.search} value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
        <Menu
          align="left"
          items={PERIODS.map((n) => ({ label: fmt(t.audit.period, { n }), checked: sinceDays === n, onSelect: () => setSinceDays(n) }))}
          trigger={(props) => (
            <Button {...props}>
              {fmt(t.audit.period, { n: sinceDays })}
              <ChevronDown size={15} aria-hidden />
            </Button>
          )}
        />
      </div>

      {audit.isRefetchError && items.length > 0 && <StaleNotice updatedAt={audit.dataUpdatedAt} onRetry={() => void audit.refetch()} />}
      {audit.isError && items.length === 0 && !audit.isPending ? (
        <ErrorPanel error={audit.error} onRetry={() => void audit.refetch()} />
      ) : !audit.isPending && items.length === 0 ? (
        <EmptyState title={t.audit.emptyTitle} body={t.audit.emptyBody} action={filtered && <Button onClick={clear}>{t.audit.clear}</Button>} />
      ) : (
        <div className="relative overflow-x-auto" aria-busy={audit.isFetching || undefined}>
          <table className={`tbl transition-opacity ${audit.isPlaceholderData ? 'opacity-60' : ''}`}>
            <thead>
              <tr>
                <th>{t.audit.colWhen}</th>
                <th>{t.audit.colWho}</th>
                <th>{t.audit.colWhat}</th>
                <th>{t.audit.colDetail}</th>
              </tr>
            </thead>
            <tbody>
              {audit.isPending ? (
                <SkeletonRows rows={8} widths={WIDTHS} />
              ) : (
                items.map((e) => (
                  <tr key={e.id}>
                    <td className="num text-[13px] text-ink-2">
                      <time dateTime={e.at}>{f.relative(e.at)}</time>
                    </td>
                    <td>{describe.actor(e)}</td>
                    <td>
                      <b className="font-semibold">{t.actions[e.action]}</b>
                    </td>
                    <td className="whitespace-normal">{describe.detail(e)}</td>
                  </tr>
                ))
              )}
              {audit.isFetchingNextPage && <SkeletonRows rows={3} widths={WIDTHS} />}
            </tbody>
          </table>
        </div>
      )}
      {audit.hasNextPage && (
        <div className="mt-4">
          <Button busy={audit.isFetchingNextPage} onClick={() => void audit.fetchNextPage()}>
            {audit.isFetchingNextPage ? t.common.loading : t.audit.more}
          </Button>
          {audit.isFetchNextPageError && <p className="mt-2 text-[13px] text-bad-text">{errorText(audit.error)}</p>}
        </div>
      )}
    </>
  )
}
