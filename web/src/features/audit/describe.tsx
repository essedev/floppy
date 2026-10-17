/* Audit events in words: a sentence for the overview's feed, a detail for the log's table. */

import type { ReactNode } from 'react'
import { fmt, plural, rich, useI18n } from '@/i18n'
import type { AuditEvent } from '@/lib/api'

const isRole = (v: unknown): v is 'admin' | 'member' => v === 'admin' || v === 'member'

/** Turns events into a sentence and a detail in the current language. */
export function useDescribe() {
  const { t, locale } = useI18n()
  const actor = (e: AuditEvent): string =>
    e.actor.kind === 'person' ? e.actor.label : e.actor.kind === 'operator' ? (e.actor.label ?? t.common.operator) : t.common.system
  const role = (v: unknown) => (isRole(v) ? t.roles[v].toLowerCase() : String(v))
  return {
    actor,
    sentence: (e: AuditEvent): ReactNode =>
      rich(t.events[e.action], { actor: actor(e), target: <b className="font-semibold">{e.target.label}</b> }),
    detail(e: AuditEvent): string {
      const parts = [e.target.label]
      const d = e.detail
      if (d.from !== undefined && d.to !== undefined) parts.push(fmt(t.details.roleChange, { from: role(d.from), to: role(d.to) }))
      else if (isRole(d.role)) parts.push(fmt(t.details.role, { role: role(d.role) }))
      if (typeof d.appVersion === 'string') parts.push(fmt(t.details.appVersion, { v: d.appVersion }))
      if (typeof d.blockSize === 'string') parts.push(fmt(t.details.blockSize, { v: d.blockSize }))
      if (typeof d.disks === 'number') parts.push(plural(t.details.disks, d.disks, locale))
      if (typeof d.macs === 'number' && d.macs > 0) parts.push(plural(t.details.macs, d.macs, locale))
      if (typeof d.reasons === 'number' && d.reasons > 0) parts.push(plural(t.details.reasons, d.reasons, locale))
      if (typeof d.reason === 'string' && d.reason in t.details.revokeReason) parts.push(t.details.revokeReason[d.reason as keyof typeof t.details.revokeReason])
      if (typeof d.code === 'string') parts.push(fmt(t.details.code, { code: d.code }))
      if (Array.isArray(d.fields) && d.fields.length > 0) {
        const names = d.fields.map((f) => (f in t.details.fieldNames ? t.details.fieldNames[f as keyof typeof t.details.fieldNames] : f))
        parts.push(fmt(t.details.fields, { list: names.join(', ') }))
      }
      return parts.join(', ')
    },
  }
}
