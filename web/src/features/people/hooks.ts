/* People: list, invite, change role, remove, resend an invite. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type InviteRequest, type Role } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** Everyone in the organization, removed people included. */
export function usePeople() {
  return useQuery({ queryKey: queryKeys.people, queryFn: () => api.people.list() })
}

function useInvalidate() {
  const client = useQueryClient()
  return (...keys: (readonly string[])[]) => Promise.all(keys.map((queryKey) => client.invalidateQueries({ queryKey })))
}

/** Sends an invite by email. */
export function useInvite() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (body: InviteRequest) => api.people.invite(body),
    onSuccess: () => invalidate(queryKeys.people, queryKeys.access, queryKeys.session, queryKeys.auditAll),
  })
}

/** Changes a person's role. */
export function useChangeRole() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) => api.people.update(id, { role }),
    onSuccess: () => invalidate(queryKeys.people, queryKeys.auditAll),
  })
}

/** Removes a person: their devices are revoked and, in manual storage, the windows of the disks they saw open. */
export function useRemovePerson() {
  const client = useQueryClient()
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (id: string) => api.people.remove(id),
    onSuccess: (result) => {
      client.setQueryData(queryKeys.key, result.key)
      return invalidate(queryKeys.people, queryKeys.access, queryKeys.devices, queryKeys.session, queryKeys.auditAll)
    },
  })
}

/** Sends the invite email again. */
export function useResendInvite() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (id: string) => api.people.resendInvite(id),
    onSuccess: () => invalidate(queryKeys.people, queryKeys.auditAll),
  })
}
