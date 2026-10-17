/**
 * How a team disk reads in the panel and in Settings: the LED (the only
 * colour), the short readout and the sentence under the name, from what the
 * engine reports. Pure, so every state is checked in tests.
 */

import { it } from "@/i18n/it";
import { gbUnit } from "@/lib/format";
import type { AuthView, DiskStatus, DiskView, TeamView } from "@/lib/types";

/** A disk row's look: `led` null is an unlit lens. */
export interface DiskLook {
  led: "ok" | "dl" | "err" | null;
  blink: boolean;
  readout: string;
  line: string;
  /** Off on this Mac: drawn quieter. */
  dim: boolean;
}

/** The look of one disk; `applying` while `disks apply` runs. */
export function diskLook(d: DiskView, applying: boolean): DiskLook {
  const t = it.disks;
  const cache = d.cache_used_mb != null && d.cache_used_mb > 0 ? t.cache(gbUnit(d.cache_used_mb)) : null;
  const line = (...parts: (string | null)[]) => parts.filter(Boolean).join(" · ");

  if (!d.enabled) {
    if (d.pending === "remove") {
      return { led: "dl", blink: false, readout: t.readout.leaving, line: t.line.leaving, dim: false };
    }
    return { led: null, blink: false, readout: t.readout.off, line: t.line.off, dim: true };
  }
  switch (d.state) {
    case null:
    case "starting":
    case "stopped":
      if (d.state === "stopped" && !applying) {
        return { led: "err", blink: false, readout: t.readout.failed, line: t.line.stopped, dim: false };
      }
      return { led: "dl", blink: true, readout: t.readout.starting, line: t.line.starting, dim: false };
    case "failed":
      return { led: "err", blink: false, readout: t.readout.failed, line: t.line.failed, dim: false };
    case "disabled":
    case "other":
      return { led: null, blink: false, readout: t.readout.unknown, line: line(cache), dim: false };
    case "mounted":
      break;
  }
  if (d.pending === "remount") {
    const lowering = d.mounted_access === "write" && d.access === "read";
    const raising = d.mounted_access === "read" && d.access === "write";
    const why = lowering ? t.line.becomingReadOnly : raising ? t.line.becomingWritable : t.line.updating;
    return { led: "dl", blink: false, readout: t.readout.waiting, line: line(why, cache), dim: false };
  }
  const readOnly = (d.mounted_access ?? d.access) === "read";
  if (d.online === false || d.meta_mode === "offline") {
    return {
      led: "err",
      blink: false,
      readout: t.readout.offline,
      line: line(readOnly ? t.line.offlineRead : t.line.offline, cache),
      dim: false,
    };
  }
  if (readOnly) {
    return { led: "ok", blink: false, readout: t.readout.readOnly, line: line(t.line.readOnly, cache), dim: false };
  }
  return { led: "ok", blink: false, readout: t.readout.mounted, line: line(cache ?? t.line.mounted), dim: false };
}

/** The team view, when this Mac shows team disks. */
export function teamOf(s: DiskStatus): TeamView | null {
  return s.mode === "team" || s.team?.refused_single ? s.team : null;
}

/** Which "no disk" sentence the panel says. */
export type NoDisksCase = "signed_out" | "registering" | "none" | "all_off";

/** Why a ready team install shows no disk. */
export function noDisksCase(team: TeamView | null, auth: AuthView | undefined): NoDisksCase {
  if (auth?.phase === "registering") return "registering";
  if (auth?.phase !== "signed_in") return "signed_out";
  const disks = team?.disks ?? [];
  return disks.length > 0 && disks.every((d) => !d.enabled) ? "all_off" : "none";
}
