/* Devices: the list (every device for admins, one's own for members) and revocation. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The devices the caller may see; refreshed every 30 s, as heartbeats arrive. */
export function useDevices() {
  return useQuery({ queryKey: queryKeys.devices, queryFn: () => api.devices.list(), refetchInterval: 30_000 })
}

/** Revokes a device's database user. */
export function useRevokeDevice() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.devices.revoke(id),
    onSuccess: async (result) => {
      client.setQueryData(queryKeys.key, result.key)
      await Promise.all(
        [queryKeys.devices, queryKeys.people, queryKeys.session, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey })),
      )
    },
  })
}
