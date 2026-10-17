/* Audit events in words: a sentence for the overview's feed, a detail for the log's table. */

import type { ReactNode } from 'react'
import { fmt, plural, rich, useI18n } from '@/i18n'
import type { AuditEvent } from '@/lib/api'

/** Turns events into a sentence and a detail in the current language. */
export function useDescribe() {
  const { t, locale } = useI18n()
  return {
    actor: (e: AuditEvent) => e.actor?.name ?? t.common.system,
    sentence: (e: AuditEvent): ReactNode =>
      rich(t.events[e.action], {
        actor: e.actor?.name ?? t.common.system,
        target: <b className="font-semibold">{e.target.label}</b>,
      }),
    detail(e: AuditEvent): string {
      const parts = [e.target.label]
      const { role, appVersion, blockSize, disks, macs } = e.detail
      if (typeof role === 'string' && (role === 'admin' || role === 'member')) parts.push(fmt(t.details.role, { role: t.roles[role].toLowerCase() }))
      if (appVersion !== undefined) parts.push(fmt(t.details.appVersion, { v: appVersion }))
      if (blockSize !== undefined) parts.push(fmt(t.details.blockSize, { v: blockSize }))
      if (typeof disks === 'number') parts.push(plural(t.details.disks, disks, locale))
      if (typeof macs === 'number' && macs > 0) parts.push(plural(t.details.macs, macs, locale))
      return parts.join(', ')
    },
  }
}
