/**
 * The account's data hooks: the view, kept current by the "auth" event from
 * every window, and one mutation per step. A step answers with the new view;
 * what went wrong is in `view.error`, a refused step in the mutation's error.
 */

import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { backend } from "@/lib/backend";
import type { AuthView, CommandError } from "@/lib/types";

export const authKey = ["auth"] as const;

/** The account as the native side sees it. */
export function useAuth() {
  const qc = useQueryClient();
  useEffect(() => backend.onAuth((v) => qc.setQueryData(authKey, v)), [qc]);
  return useQuery({ queryKey: authKey, queryFn: backend.authView, staleTime: Infinity });
}

/** A sign-in step; its answer replaces the view. */
export function useAuthStep<T = void>(fn: (arg: T) => Promise<AuthView>) {
  const qc = useQueryClient();
  return useMutation<AuthView, CommandError, T>({
    mutationFn: fn,
    onSuccess: (view) => qc.setQueryData(authKey, view),
  });
}
