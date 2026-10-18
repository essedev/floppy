/* Disks: list, create with their bucket and access, change cache policy and trash, probe, delete. */

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
        // A ready disk has an instance, a probe and the users of its people now.
        await Promise.all([queryKeys.disks, queryKeys.session, queryKeys.access, queryKeys.health, queryKeys.key].map((queryKey) => client.invalidateQueries({ queryKey, exact: true })))
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
      Promise.all([queryKeys.disks, queryKeys.access, queryKeys.key, queryKeys.session, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey }))),
  })
}

/** A test write on a disk's bucket; the result lands in its storage status. */
export function useProbeDisk() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.disks.probe(id),
    onSettled: () => Promise.all([queryKeys.health, queryKeys.key].map((queryKey) => client.invalidateQueries({ queryKey }))),
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

/** Deletes a disk: off every device and list; its data stays at the provider. */
export function useDeleteDisk() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.disks.remove(id),
    onSuccess: () =>
      Promise.all([queryKeys.disks, queryKeys.access, queryKeys.key, queryKeys.session, queryKeys.health, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey }))),
  })
}
