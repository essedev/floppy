/* Session and sign-in requests through TanStack Query. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Session } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The current session; data is null when signed out. */
export function useSession() {
  return useQuery({ queryKey: queryKeys.session, queryFn: () => api.auth.session(), staleTime: 60_000 })
}

/** The session of a signed-in page; the guard guarantees it is there. */
export function useCurrentSession(): Session {
  const { data } = useSession()
  if (!data) throw new Error('useCurrentSession outside a signed-in route')
  return data
}

/** Asks for a magic link and a code. */
export function useStartSignIn() {
  return useMutation({ mutationFn: (email: string) => api.auth.start(email) })
}

/** Polls a pending request until the link is opened somewhere. */
export function useSignInStatus(requestId: string | null) {
  return useQuery({
    queryKey: queryKeys.signIn(requestId ?? ''),
    queryFn: () => api.auth.status(requestId ?? ''),
    enabled: requestId !== null,
    refetchInterval: (query) => (query.state.data?.status === 'pending' || query.state.data === undefined ? 2000 : false),
    refetchIntervalInBackground: true,
    retry: false,
  })
}

/** Signs in with the 6-digit code. */
export function useVerifyCode() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ requestId, code }: { requestId: string; code: string }) => api.auth.verifyCode(requestId, code),
    onSuccess: (session) => client.setQueryData(queryKeys.session, session),
  })
}

/** Signs in with the token of a magic link. */
export function useOpenLink() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (token: string) => api.auth.openLink(token),
    onSuccess: (session) => client.setQueryData(queryKeys.session, session),
  })
}

/** Signs out and forgets every cached answer. */
export function useSignOut() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.auth.signOut(),
    onSuccess: () => {
      client.clear()
      client.setQueryData(queryKeys.session, null)
    },
  })
}
