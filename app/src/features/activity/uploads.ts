/**
 * What this Mac's uploads say in the panel (concept H, variant c3): the state
 * that takes the panel's title, a disk's line and a file's row. Only large
 * writes reach here (activity.rs, `UPLOAD_SHOWN`): an app's autosave never
 * changes the title.
 */

import { it } from "@/i18n/it";
import { bytes, finderName } from "@/lib/format";
import type { Sending, UploadFile, UploadsView } from "@/lib/types";

/** The state an upload puts first, most urgent first. */
export type UploadPhase = "incomplete" | "paused" | "sending" | "check" | "done";

/** A finished upload says "Tutto caricato" this long. */
export const DONE_SHOWN_S = 300;

export interface UploadSummary {
  phase: UploadPhase | null;
  /** Files on their way. */
  moving: Sending[];
  /** Bytes still to send, and bytes a second (0 while unknown). */
  waiting: number;
  rate: number;
  paused: boolean;
  incomplete: UploadFile[];
  check: UploadFile[];
  /** Recent files whose data is in the bucket. */
  done: UploadFile[];
}

const EMPTY: UploadsView = { sending: [], disks: [], files: [] };

/** The uploads overall, or of one disk when `disk` is given (null: the single-user volume). */
export function uploadSummary(u: UploadsView | undefined, disk?: string | null, now = Date.now() / 1000): UploadSummary {
  const v = u ?? EMPTY;
  const mine = <T extends { disk: string | null }>(xs: T[]) => (disk === undefined ? xs : xs.filter((x) => x.disk === disk));
  const disks = mine(v.disks);
  const files = mine(v.files);
  const moving = mine(v.sending);
  const waiting = disks.reduce((s, d) => s + d.waiting, 0);
  const rate = disks.reduce((s, d) => s + d.rate, 0);
  const paused = disks.some((d) => d.paused && d.waiting > 0);
  const incomplete = files.filter((f) => f.state === "incomplete");
  const check = files.filter((f) => f.state === "check");
  // A file being checked is in the bucket already: a close waits for its blocks.
  const done = files.filter((f) => (f.state === "done" || f.state === "checking") && now - f.closed <= DONE_SHOWN_S);
  const phase: UploadPhase | null = incomplete.length
    ? "incomplete"
    : paused
      ? "paused"
      : moving.length || waiting > 0
        ? "sending"
        : check.length
          ? "check"
          : done.length
            ? "done"
            : null;
  return { phase, moving, waiting, rate, paused, incomplete, check, done };
}

/** "circa 5 minuti", or null while the speed is not known. */
export function etaText(waiting: number, rate: number): string | null {
  if (waiting <= 0 || rate <= 0) return null;
  const s = waiting / rate;
  if (s < 60) return it.uploads.eta.lessThanMinute;
  const m = Math.round(s / 60);
  if (m < 60) return it.uploads.eta.minutes(m);
  const h = Math.floor(m / 60);
  return it.uploads.eta.hours(h, Math.round((m % 60) / 10) * 10);
}

const nameOf = (path: string) => finderName(path.split("/").pop() ?? path);

/** The panel's title and body when an upload comes first; null when it does not. */
export function uploadTitle(s: UploadSummary): { title: string; body: string } | null {
  const t = it.uploads.title;
  const b = it.uploads.body;
  const left = bytes(s.waiting);
  switch (s.phase) {
    case "incomplete":
      return { title: t.incomplete(s.incomplete.length), body: b.incomplete(s.incomplete.length === 1 ? nameOf(s.incomplete[0]!.path) : null) };
    case "paused":
      return { title: t.paused, body: b.paused(left) };
    case "sending": {
      const n = s.moving.length;
      if (s.waiting <= 0) return { title: n ? t.sending(n) : t.sendingRest, body: b.copying };
      return { title: n ? t.sending(n) : t.sendingRest, body: b.sending(left, etaText(s.waiting, s.rate)) };
    }
    case "check":
      return { title: t.check(s.check.length), body: b.check(nameOf(s.check[0]!.path)) };
    case "done":
      return { title: t.done, body: b.done(s.done.length, s.done.length === 1 ? nameOf(s.done[0]!.path) : null) };
    case null:
      return null;
  }
}

/** A disk row's line and LED while it has uploads; null when it has none. */
export function uploadDiskLine(s: UploadSummary): { line: string; led: "dl" | "err"; blink: boolean } | null {
  const d = it.uploads.disk;
  switch (s.phase) {
    case "incomplete":
      return { line: d.incomplete(s.incomplete.length), led: "err", blink: false };
    case "paused":
      return { line: d.paused, led: "dl", blink: false };
    case "sending":
      return s.moving.length
        ? { line: d.sending(s.moving.length, etaText(s.waiting, s.rate)), led: "dl", blink: true }
        : { line: d.sendingRest(bytes(s.waiting)), led: "dl", blink: true };
    case "check":
      return { line: d.check(s.check.length), led: "dl", blink: false };
    default:
      return null;
  }
}

/** One file of an upload, as its row shows it. */
export interface UploadRow {
  key: string;
  name: string;
  line: string;
  readout: string;
  led: "ok" | "dl" | "err";
  blink: boolean;
  /** Its outcome, for the row's menu; null while it is on its way. */
  file: UploadFile | null;
}

/** The rows a disk shows: the problems always, the rest behind "Mostra i file". */
export function uploadRows(s: UploadSummary): { pinned: UploadRow[]; rest: UploadRow[] } {
  const r = it.uploads.row;
  const ro = it.uploads.readout;
  const outcome = (f: UploadFile): UploadRow => {
    const base = { key: `f:${f.disk}:${f.path}`, name: nameOf(f.path), file: f };
    if (f.state === "incomplete") {
      const line = f.reason === "video" ? r.incompleteVideo : r.incompleteFinder(bytes(f.size ?? 0));
      return { ...base, line, readout: ro.incomplete, led: "err", blink: false };
    }
    if (f.state === "check") return { ...base, line: r.check, readout: ro.check, led: "dl", blink: false };
    return { ...base, line: r.done(bytes(f.size ?? 0)), readout: ro.done, led: "ok", blink: false };
  };
  const moving = s.moving.map(
    (m): UploadRow => ({
      key: `s:${m.disk}:${m.path}`,
      name: nameOf(m.path),
      line: m.copying ? r.copying(bytes(m.size)) : s.paused ? r.waiting : r.sending,
      readout: s.paused ? ro.paused : m.copying ? ro.copying : ro.sending,
      led: "dl",
      blink: !s.paused,
      file: null,
    }),
  );
  return {
    pinned: [...s.incomplete, ...s.check].map(outcome),
    rest: [...moving, ...[...s.done].reverse().map(outcome)],
  };
}
