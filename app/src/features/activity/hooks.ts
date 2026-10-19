/** The activity's live data: the panel's notices and what is being written, pushed as "activity". */

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { backend } from "@/lib/backend";

export const activityKeys = {
  snapshot: ["activity-snapshot"] as const,
  feed: (disk: string | null) => ["activity", disk ?? "-"] as const,
};

/** The notices and the files being written, kept current by the "activity" event. */
export function useActivitySnapshot() {
  const qc = useQueryClient();
  useEffect(() => backend.onActivity((s) => qc.setQueryData(activityKeys.snapshot, s)), [qc]);
  return useQuery({ queryKey: activityKeys.snapshot, queryFn: backend.getActivitySnapshot, staleTime: Infinity });
}
