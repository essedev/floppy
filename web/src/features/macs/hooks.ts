/* Macs: the list (every Mac for admins, one's own for members) and revocation. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The Macs the caller may see; refreshed every 30 s, as heartbeats arrive. */
export function useMacs() {
  return useQuery({ queryKey: queryKeys.macs, queryFn: () => api.macs.list(), refetchInterval: 30_000 })
}

/** Revokes a Mac's database user. */
export function useRevokeMac() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.macs.revoke(id),
    onSuccess: async (result) => {
      client.setQueryData(queryKeys.key, result.key)
      await Promise.all(
        [queryKeys.macs, queryKeys.people, queryKeys.session, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey })),
      )
    },
  })
}
