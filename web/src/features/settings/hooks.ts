/* Organization settings, the test email and the storage probe; health for the overview. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type StorageProbeRequest, type UpdateSettingsRequest } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The organization's settings. */
export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: () => api.settings.get() })
}

/** Saves settings; the organization's name also lives in the session. */
export function useSaveSettings() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateSettingsRequest) => api.settings.update(body),
    onSuccess: (settings) => {
      client.setQueryData(queryKeys.settings, settings)
      return Promise.all([queryKeys.session, queryKeys.auditAll, queryKeys.health].map((queryKey) => client.invalidateQueries({ queryKey })))
    },
  })
}

/** Sends a test email to the caller. */
export function useTestMail() {
  const client = useQueryClient()
  return useMutation({ mutationFn: () => api.settings.testMail(), onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.health }) })
}

/** Tries a write to a bucket, changing nothing. */
export function useProbeStorage() {
  return useMutation({ mutationFn: (body: StorageProbeRequest) => api.settings.probeStorage(body) })
}

/** Metadata, storage and mail as the control plane sees them; refreshed every 30 s. */
export function useHealth() {
  return useQuery({ queryKey: queryKeys.health, queryFn: () => api.health.get(), refetchInterval: 30_000 })
}
