/* The storage keys: each disk's (manual) or each device's (managed), rotations and windows. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type KeyStatus, type StartRotationRequest } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** How often to poll: fast while a rotation applies, slower while one awaits the old key's
 * deletion (for the informative devices count) or a revoked device's key is being deleted. */
function pollEvery(status: KeyStatus | undefined): number | false {
  const states = new Set(status?.disks.map((d) => d.rotation?.state))
  if (states.has('applying')) return 700
  if (states.has('awaiting_old_key_deletion') || status?.keys.some((k) => k.state !== 'active')) return 3000
  return false
}

/** The keys, their windows and rotations; `enabled` false for members, who do not see them. */
export function useKeyStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.key,
    queryFn: () => api.key.get(),
    enabled,
    refetchInterval: (query) => pollEvery(query.state.data),
  })
}

/** The disks whose key window is open, the earliest first. */
export function openWindows(status: KeyStatus | undefined) {
  return (status?.disks ?? [])
    .flatMap((d) => (d.window ? [{ disk: d, window: d.window }] : []))
    .sort((a, b) => a.window.openedAt.localeCompare(b.window.openedAt))
}

/** Starts a rotation of one disk's key with a new key. */
export function useStartRotation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: StartRotationRequest) => api.key.startRotation(body),
    onSuccess: () => Promise.all([queryKeys.key, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey }))),
  })
}

/** Confirms the old key is deleted at the provider: the disk's window closes. */
export function useCloseWindow() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (rotationId: string) => api.key.close(rotationId),
    onSuccess: (status) => {
      client.setQueryData(queryKeys.key, status)
      return client.invalidateQueries({ queryKey: queryKeys.auditAll })
    },
  })
}
