/* The audit log, cursor-paginated, and its latest events for the overview. */

import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { api, type AuditQuery } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** Pages of the log for a filter. */
export function useAudit(filter: Omit<AuditQuery, 'cursor'>) {
  return useInfiniteQuery({
    queryKey: queryKeys.audit(filter),
    queryFn: ({ pageParam }) => api.audit.list({ ...filter, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    placeholderData: keepPreviousData,
  })
}

/** The latest events, for the overview's activity panel. */
export function useRecentActivity(limit = 6) {
  return useQuery({
    queryKey: [...queryKeys.auditAll, 'recent', limit],
    queryFn: () => api.audit.list({ sinceDays: 90, limit }),
  })
}
