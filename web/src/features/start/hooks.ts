/* The start page's reads: the disks the signed-in person sees, their own devices, the server address. */

import { useQuery } from '@tanstack/react-query'
import { api, type Device } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The ready disks the caller may read or write. */
export function useMyDisks() {
  return useQuery({ queryKey: queryKeys.myDisks, queryFn: () => api.access.mine() })
}

/** The caller's own devices that still have access. */
export function ownLive(devices: Device[] | undefined, userId: string): Device[] {
  return devices?.filter((d) => d.owner.id === userId && !d.revokedAt) ?? []
}

/**
 * What to type as the app's server: this control plane's host, the scheme only when it is not
 * https (the app adds https to a bare host). The API is same origin unless VITE_API_URL says
 * otherwise; the app strips the `/api/v1` itself.
 */
export function serverAddress(): string {
  const base = new URL(import.meta.env.VITE_API_URL ?? '/', window.location.href)
  return base.protocol === 'https:' ? base.host : base.origin
}
