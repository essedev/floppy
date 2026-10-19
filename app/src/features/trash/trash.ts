/**
 * The trash window's rules, apart from its markup: days, filters, groups and
 * the line under each name (concept-f-trash-activity, 1B).
 */

import { it } from "@/i18n/it";
import type { Restored, TrashDisk, TrashEntry } from "@/lib/types";

export type KindFilter = "all" | "deleted" | "replaced";

const DAY = 86400;

/** The disk's name in the window: its folder, or "Floppy" for the single-user volume. */
export function diskName(d: TrashDisk): string {
  return d.folder ?? it.trash.thisDisk;
}

/** A stable key for a disk, the single-user volume included. */
export function diskKey(d: Pick<TrashDisk, "disk">): string {
  return d.disk ?? "-";
}

function midnight(ts: number): number {
  const d = new Date(ts * 1000);
  d.setHours(0, 0, 0, 0);
  return d.getTime() / 1000;
}

const dateFmt = new Intl.DateTimeFormat("it-IT", { weekday: "long", day: "numeric", month: "long" });
const timeFmt = new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit" });

/** Today, yesterday or the weekday and date, for the day of `ts` seen from `now` (seconds). */
export function dayLabel(ts: number, now: number): string {
  const days = Math.round((midnight(now) - midnight(ts)) / DAY);
  if (days === 0) return it.trash.today;
  if (days === 1) return it.trash.yesterday;
  const s = dateFmt.format(new Date(ts * 1000));
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function timeOf(ts: number): string {
  return timeFmt.format(new Date(ts * 1000));
}

/** Whole days before the entry leaves the trash; 0 when it goes today. */
export function daysLeft(expires: number, now: number): number {
  return Math.max(0, Math.floor((expires - now) / DAY));
}

export function matches(e: TrashEntry, q: string, kind: KindFilter): boolean {
  if (kind === "deleted" && e.replaced) return false;
  if (kind === "replaced" && !e.replaced) return false;
  const needle = q.trim().toLocaleLowerCase("it");
  if (!needle) return true;
  return `${e.name}\n${e.from ?? ""}`.toLocaleLowerCase("it").includes(needle);
}

export interface DayGroup {
  label: string;
  /** Days left for the first entry of the day to go. */
  left: number;
  items: TrashEntry[];
}

/** Entries by day of deletion, newest first. */
export function byDay(entries: TrashEntry[], now: number): DayGroup[] {
  const groups = new Map<number, TrashEntry[]>();
  for (const e of [...entries].sort((a, b) => b.deleted_at - a.deleted_at)) {
    const k = midnight(e.deleted_at);
    groups.set(k, [...(groups.get(k) ?? []), e]);
  }
  return [...groups.entries()].map(([day, items]) => ({
    label: dayLabel(day, now),
    left: daysLeft(Math.min(...items.map((e) => e.expires_at)), now),
    items,
  }));
}

/** Where the entry was: its folder, the disk's name at the top. */
function origin(e: TrashEntry, disk: string): string | null {
  if (e.from === null) return null;
  return e.from === "" ? disk : e.from;
}

/** The line under the name. */
export function fromLine(e: TrashEntry, disk: string): string {
  const where = origin(e, disk);
  if (where === null) return it.trash.gone;
  if (e.kind === "folder") return it.trash.folderFrom(e.files ?? 0, where);
  if (e.replaced) return it.trash.replacedFrom(where);
  return it.trash.from(where);
}

export function restoreLabel(e: TrashEntry): string {
  if (e.kind === "folder") return it.trash.restoreFolder;
  return e.replaced ? it.trash.restoreCopy : it.trash.restore;
}

export function restoredLine(r: Restored): string {
  if (r.moved) return it.trash.restoredTop;
  return r.renamed ? it.trash.restoredAs(r.name) : it.trash.restored;
}
