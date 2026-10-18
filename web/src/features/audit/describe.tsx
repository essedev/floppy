/* Audit events in words: a sentence for the overview's feed, a detail for the log's table. */

import type { ReactNode } from 'react'
import { fmt, plural, rich, useI18n } from '@/i18n'
import type { AccessLevel, AuditEvent } from '@/lib/api'
import { isProvider, providerName } from '@/lib/providers'

const isRole = (v: unknown): v is 'admin' | 'member' => v === 'admin' || v === 'member'
const isAccess = (v: unknown): v is AccessLevel => v === 'none' || v === 'read' || v === 'write'
const has = <T extends object>(table: T, key: unknown): key is keyof T => typeof key === 'string' && key in table

/** Turns events into a sentence and a detail in the current language. */
export function useDescribe() {
  const { t, locale } = useI18n()
  const actor = (e: AuditEvent): string =>
    e.actor.kind === 'person' ? e.actor.label : e.actor.kind === 'operator' ? (e.actor.label ?? t.common.operator) : t.common.system
  const role = (v: unknown) => (isRole(v) ? t.roles[v].toLowerCase() : String(v))
  const access = (v: unknown) => (isAccess(v) ? t.access.levels[v].toLowerCase() : String(v))
  return {
    actor,
    sentence: (e: AuditEvent): ReactNode =>
      rich(t.events[e.action], { actor: actor(e), target: <b className="font-semibold">{e.target.label}</b> }),
    detail(e: AuditEvent): string {
      const parts = [e.target.label]
      const d = e.detail
      if (e.action === 'disk_access_changed') {
        parts.push(fmt(t.details.accessChange, { person: String(d.person ?? ''), from: access(d.from), to: access(d.to) }))
      } else if (d.from !== undefined && d.to !== undefined) {
        parts.push(fmt(t.details.roleChange, { from: role(d.from), to: role(d.to) }))
      } else if (isRole(d.role)) {
        parts.push(fmt(t.details.role, { role: role(d.role) }))
      }
      if (typeof d.disk === 'string' && d.disk) parts.push(fmt(t.details.disk, { disk: d.disk }))
      if (has(t.details.keyHolder, d.holder)) parts.push(t.details.keyHolder[d.holder])
      if (typeof d.for === 'string' && d.for) parts.push(fmt(t.details.keyFor, { name: d.for }))
      if (d.by === 'adapter') parts.push(t.details.byAdapter)
      if (has(t.details.storageMode, d.storageMode)) parts.push(t.details.storageMode[d.storageMode])
      if (typeof d.provider === 'string' && d.provider) parts.push(fmt(t.details.provider, { provider: isProvider(d.provider) ? providerName(d.provider) : d.provider }))
      if (has(t.details.via, d.via)) parts.push(t.details.via[d.via])
      if (typeof d.appVersion === 'string') parts.push(fmt(t.details.appVersion, { v: d.appVersion }))
      if (typeof d.blockSize === 'string') parts.push(fmt(t.details.blockSize, { v: d.blockSize }))
      if (typeof d.bucket === 'string' && d.bucket) parts.push(fmt(t.details.bucket, { bucket: d.bucket }))
      for (const key of ['trashDays', 'cacheExpireDays'] as const) {
        const change = d[key]
        if (Array.isArray(change) && change.length === 2) parts.push(fmt(t.details[key], { from: String(change[0]), to: String(change[1]) }))
      }
      if (typeof d.devices === 'number' && d.devices > 0) parts.push(plural(t.details.devices, d.devices, locale))
      if (typeof d.reasons === 'number' && d.reasons > 0) parts.push(plural(t.details.reasons, d.reasons, locale))
      if (has(t.details.revokeReason, d.reason)) parts.push(t.details.revokeReason[d.reason])
      if (has(t.details.step, d.step)) parts.push(t.details.step[d.step])
      if (typeof d.code === 'string') parts.push(fmt(t.details.code, { code: d.code }))
      if (Array.isArray(d.fields) && d.fields.length > 0) {
        const names = d.fields.map((f) => (has(t.details.fieldNames, f) ? t.details.fieldNames[f] : String(f)))
        parts.push(fmt(t.details.fields, { list: [...new Set(names)].join(', ') }))
      }
      return parts.join(', ')
    },
  }
}
