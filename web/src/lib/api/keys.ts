/* TanStack Query keys, one place, so mutations invalidate the right lists. */

import type { AuditQuery } from './types'

/** Query keys of every API read. */
export const queryKeys = {
  session: ['session'] as const,
  people: ['people'] as const,
  macs: ['macs'] as const,
  disks: ['disks'] as const,
  key: ['key'] as const,
  rotation: (id: string) => ['key', 'rotation', id] as const,
  audit: (q: Omit<AuditQuery, 'cursor'>) => ['audit', q] as const,
  auditAll: ['audit'] as const,
  health: ['health'] as const,
  settings: ['settings'] as const,
  signIn: (id: string) => ['sign-in', id] as const,
}
