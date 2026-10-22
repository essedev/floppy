/* Claude: the MCP address and commands, the person's connections, and the consent's request. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type McpScope } from '@/lib/api'
import { queryKeys } from '@/lib/api/keys'

/** The MCP address and the commands that connect Claude. */
export function useMcpInstall() {
  return useQuery({ queryKey: queryKeys.mcpInstall, queryFn: () => api.mcp.install(), staleTime: Infinity })
}

/** The signed-in person's connected MCP clients. */
export function useConnections() {
  return useQuery({ queryKey: queryKeys.connections, queryFn: () => api.connections.list() })
}

/** Disconnects one client: both its tokens stop at once. */
export function useRevokeConnection() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.connections.revoke(id),
    onSuccess: () => Promise.all([queryKeys.connections, queryKeys.auditAll].map((queryKey) => client.invalidateQueries({ queryKey }))),
  })
}

/** An open authorization request; a 410 means decided or expired, never retried. */
export function useConsentRequest(id: string) {
  return useQuery({ queryKey: queryKeys.consent(id), queryFn: () => api.oauth.request(id), retry: false, enabled: id !== '' })
}

/** Approves or denies a request; the caller sends the browser where the answer says. */
export function useDecide(id: string) {
  return useMutation({
    mutationFn: (decision: { approve: true; scopes: McpScope[] } | { approve: false }) =>
      decision.approve ? api.oauth.approve(id, decision.scopes) : api.oauth.deny(id),
  })
}
