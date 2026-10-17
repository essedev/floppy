/* The S3 key: its status, starting a rotation, following it, closing the window. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type StartRotationRequest } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The key, its risk window and any rotation; polled fast while a rotation applies. */
export function useKeyStatus() {
  return useQuery({
    queryKey: queryKeys.key,
    queryFn: () => api.key.get(),
    // Fast while applying; slower while awaiting, for the informative Macs count.
    refetchInterval: (query) => {
      const state = query.state.data?.rotation?.state
      return state === 'applying' ? 700 : state === 'awaiting_old_key_deletion' ? 3000 : false
    },
  })
}

/** Starts a rotation with a new key. */
export function useStartRotation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: StartRotationRequest) => api.key.startRotation(body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.key }),
  })
}

/** Confirms the old key is deleted at the provider: the window closes. */
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
