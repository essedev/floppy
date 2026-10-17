/* Disks: list, create, change cache policy and trash. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type CreateDiskRequest, type UpdateDiskRequest } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The organization's disks. */
export function useDisks() {
  return useQuery({ queryKey: queryKeys.disks, queryFn: () => api.disks.list() })
}

/** Creates a disk (juicefs format on a new prefix). */
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
