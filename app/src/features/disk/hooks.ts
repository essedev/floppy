/**
 * Data hooks for every view. The native side pushes the status and the setup
 * progress as events; the queries hold the last value and the actions are
 * mutations, so every view has its loading and error states.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { backend } from "@/lib/backend";
import type { CommandError, DiskStatus, SetupProgress } from "@/lib/types";

export const keys = {
  status: ["status"] as const,
  setup: ["setup"] as const,
  info: ["app-info"] as const,
  folders: ["folders"] as const,
  login: ["login"] as const,
  update: ["app-update"] as const,
};

/** The disk's status, kept current by the "status" event. */
export function useStatus() {
  const qc = useQueryClient();
  useEffect(() => backend.onStatus((s) => qc.setQueryData(keys.status, s)), [qc]);
  return useQuery({ queryKey: keys.status, queryFn: backend.getStatus, staleTime: Infinity });
}

/** Where `floppy setup` is, kept current by the "setup" event. */
export function useSetup() {
  const qc = useQueryClient();
  useEffect(() => backend.onSetup((s) => qc.setQueryData(keys.setup, s)), [qc]);
  return useQuery<SetupProgress>({ queryKey: keys.setup, queryFn: backend.getSetup, staleTime: Infinity });
}

/** A newer release of the app, kept current by the "app-update" event. */
export function useAppUpdate() {
  const qc = useQueryClient();
  useEffect(() => backend.onAppUpdate((u) => qc.setQueryData(keys.update, u)), [qc]);
  return useQuery({ queryKey: keys.update, queryFn: backend.getAppUpdate, staleTime: Infinity });
}

export function useAppInfo() {
  return useQuery({ queryKey: keys.info, queryFn: backend.appInfo, staleTime: Infinity });
}

/** The folders at the top of the disk, or of `path` on it, read only while it is connected. */
export function useFolders(enabled: boolean, path?: string) {
  return useQuery({
    queryKey: [...keys.folders, path ?? null],
    queryFn: () => backend.listFolders(path),
    enabled,
    retry: false,
    staleTime: 10_000,
  });
}

/** The login item, through the autostart plugin. */
export function useLogin() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: keys.login, queryFn: backend.loginEnabled });
  const set = useMutation({
    mutationFn: backend.setLogin,
    onSettled: () => qc.invalidateQueries({ queryKey: keys.login }),
  });
  return { query, set };
}

/** A command on the disk; its error is a `CommandError` for the dictionary. */
export function useDiskAction<T = void>(fn: (arg: T) => Promise<void>) {
  return useMutation<void, CommandError, T>({ mutationFn: fn });
}

/** Seconds left to the next check, counting down each second. */
export function useCountdown(status: DiskStatus | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!status) return 0;
  const due = status.checked_ms + status.next_check_s * 1000;
  return Math.max(0, Math.ceil((due - now) / 1000));
}
