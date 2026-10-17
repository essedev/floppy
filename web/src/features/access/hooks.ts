/* Access per person and disk: the whole matrix, and setting one cell. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type AccessLevel, type DiskAccess } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The three levels, from closed to open. */
export const ACCESS_LEVELS: AccessLevel[] = ['none', 'read', 'write']

/** Every (disk, person) pair; polled while a change is still owed to someone's devices. */
export function useAccess() {
  return useQuery({
    queryKey: queryKeys.access,
    queryFn: () => api.access.list(),
    refetchInterval: (query) => (query.state.data?.some((a) => !a.applied) ? 2500 : false),
  })
}

/** The access of one person to one disk, `none` when the pair is not listed. */
export function accessOf(list: DiskAccess[] | undefined, diskId: string, personId: string): DiskAccess {
  return list?.find((a) => a.diskId === diskId && a.personId === personId) ?? { diskId, personId, access: 'none', applied: true }
}

/** Sets a person's access to a disk; the answer replaces that cell at once. */
export function useSetAccess() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ diskId, personId, access }: { diskId: string; personId: string; access: AccessLevel }) => api.access.set(diskId, personId, access),
    onSuccess: (entry) => {
      client.setQueryData<DiskAccess[]>(queryKeys.access, (list) => {
        if (!list) return list
        const others = list.filter((a) => a.diskId !== entry.diskId || a.personId !== entry.personId)
        return [...others, entry]
      })
      // Lowering to none can open a key window (manual storage).
      return Promise.all([queryKeys.access, queryKeys.key, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey })))
    },
  })
}
