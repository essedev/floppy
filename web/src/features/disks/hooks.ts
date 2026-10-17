/* Disks: list, create, change cache policy and trash. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CreateDiskRequest, type UpdateDiskRequest } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The organization's disks; refreshed often while one is being created. */
export function useDisks() {
  return useQuery({
    queryKey: queryKeys.disks,
    queryFn: () => api.disks.list(),
    refetchInterval: (query) => (query.state.data?.some((d) => d.state === 'creating') ? 1500 : false),
  })
}

/** One disk being created, polled until its format is `ready` or `failed`. */
export function useDiskCreation(id: string | null) {
  const client = useQueryClient()
  return useQuery({
    queryKey: [...queryKeys.disks, 'creation', id],
    queryFn: async () => {
      const disk = await api.disks.get(id ?? '')
      if (disk.state !== 'creating') {
        await Promise.all([queryKeys.disks, queryKeys.session].map((queryKey) => client.invalidateQueries({ queryKey, exact: true })))
      }
      return disk
    },
    enabled: id !== null,
    refetchInterval: (query) => (query.state.data?.state === 'creating' || query.state.data === undefined ? 1000 : false),
  })
}

/** Creates a disk: answers `creating`, the format runs after. */
export function useCreateDisk() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateDiskRequest) => api.disks.create(body),
    onSuccess: () =>
      Promise.all([queryKeys.disks, queryKeys.session, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey }))),
  })
}

/** Changes a disk's trash days or cache policy. */
export function useUpdateDisk() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateDiskRequest }) => api.disks.update(id, body),
    onSuccess: () => Promise.all([queryKeys.disks, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey }))),
  })
}
