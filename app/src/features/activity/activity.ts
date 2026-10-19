/**
 * The Activity tab's rules, apart from its markup (concept-f-trash-activity
 * 2A, DECISIONS #27): close operations of one person in one folder become one
 * row, a deleted folder takes its files with it, and each row says what
 * happened in one sentence. Also the live lines of the panel's projects.
 */

import { it } from "@/i18n/it";
import { bytes } from "@/lib/format";
import type { FeedEvent, LiveFile, Op, PersonRef } from "@/lib/types";

/** The type filter: added (and folders made), renamed or moved, deleted or replaced. */
export type TypeFilter = "all" | "add" | "ren" | "del";

/** Operations of one person in one folder this close together are one row. */
export const GROUP_GAP_S = 120;
/** A folder's files are deleted around the folder itself. */
const FOLDER_GAP_S = 300;

/** A row of the feed: one event, or several close ones. */
export interface Group {
  id: string;
  op: Op;
  kind: "file" | "folder";
  by: PersonRef;
  this_mac: boolean;
  /** The newest event's time. */
  at: number;
  /** The folder it happened in, relative to the disk ("" at the top). */
  folder: string;
  /** The event that names the row: the folder of a folder delete, else the newest. */
  head: FeedEvent;
  /** Every event in the row, the head included, newest first. */
  events: FeedEvent[];
  /** Files in the row, and their bytes when known. */
  files: number;
  bytes: number;
}

export function parentOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

export function nameOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

function under(path: string, folder: string): boolean {
  return path === folder || path.startsWith(`${folder}/`);
}

/** Who as a key: this Mac's person, or a colleague by name. */
export function personKey(by: PersonRef): string {
  return by.you ? "\u0000you" : by.name;
}

export function who(by: PersonRef): string {
  return by.you ? it.activity.you : by.name || it.activity.someone;
}

const MERGES = new Set<Op>(["added", "written", "replaced", "deleted"]);

/** The feed's rows, newest first, from events in any order. */
export function group(events: FeedEvent[]): Group[] {
  const asc = [...events].sort((a, b) => a.at - b.at || a.id.localeCompare(b.id));
  const taken = new Set<string>();
  const groups: Group[] = [];
  const make = (head: FeedEvent, members: FeedEvent[]): Group => {
    const files = members.filter((e) => e.kind === "file");
    return {
      id: head.id,
      op: head.op,
      kind: head.kind,
      by: head.by,
      this_mac: head.this_mac,
      at: Math.max(...members.map((e) => e.at)),
      folder: parentOf(head.path),
      head,
      events: [...members].sort((a, b) => b.at - a.at),
      files: files.length,
      bytes: files.reduce((n, e) => n + (e.size ?? 0), 0),
    };
  };
  // A deleted folder takes the deletes inside it, whatever their folder.
  const folders = asc.filter((e) => e.op === "deleted" && e.kind === "folder");
  for (const f of folders) {
    if (taken.has(f.id)) continue;
    const inside = asc.filter(
      (e) =>
        e.id !== f.id &&
        !taken.has(e.id) &&
        e.op === "deleted" &&
        personKey(e.by) === personKey(f.by) &&
        under(e.path, f.path) &&
        Math.abs(e.at - f.at) <= FOLDER_GAP_S,
    );
    // A folder inside another deleted folder belongs to the outer one.
    const outer = folders.find(
      (g) => g.id !== f.id && personKey(g.by) === personKey(f.by) && f.path.startsWith(`${g.path}/`) && Math.abs(g.at - f.at) <= FOLDER_GAP_S,
    );
    if (outer) continue;
    for (const e of [f, ...inside]) taken.add(e.id);
    groups.push(make(f, [f, ...inside]));
  }
  let open: { head: FeedEvent; members: FeedEvent[] } | null = null;
  const close = () => {
    if (open) groups.push(make(open.members[open.members.length - 1]!, open.members));
    open = null;
  };
  for (const e of asc) {
    if (taken.has(e.id)) continue;
    const last: FeedEvent | undefined = open?.members[open.members.length - 1];
    const joins =
      open &&
      last &&
      MERGES.has(e.op) &&
      e.kind === "file" &&
      e.op === open.head.op &&
      personKey(e.by) === personKey(open.head.by) &&
      e.disk === open.head.disk &&
      parentOf(e.path) === parentOf(open.head.path) &&
      e.at - last.at <= GROUP_GAP_S;
    if (joins) {
      open!.members.push(e);
      continue;
    }
    close();
    if (MERGES.has(e.op) && e.kind === "file") open = { head: e, members: [e] };
    else groups.push(make(e, [e]));
  }
  close();
  return groups.sort((a, b) => b.at - a.at || b.id.localeCompare(a.id));
}

export function typeOf(op: Op): TypeFilter | null {
  if (op === "created" || op === "added") return "add";
  if (op === "renamed") return "ren";
  if (op === "deleted" || op === "replaced") return "del";
  return null;
}

export function matches(g: Group, person: string, type: TypeFilter): boolean {
  if (person !== "all" && personKey(g.by) !== person) return false;
  return type === "all" || typeOf(g.op) === type;
}

/** The people in the feed, this Mac's person first. */
export function people(groups: Group[]): { key: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const g of groups) seen.set(personKey(g.by), who(g.by));
  return [...seen.entries()]
    .sort(([a], [b]) => (a.startsWith("\u0000") ? -1 : b.startsWith("\u0000") ? 1 : a.localeCompare(b, "it")))
    .map(([key, label]) => ({ key, label }));
}

/** The folder's name in a sentence: the disk's own at the top. */
function place(folder: string, disk: string): string {
  return folder === "" ? disk : folder;
}

/** One row's sentence: who in bold, then what. */
export function sentence(g: Group, disk: string): { who: string; what: string } {
  const a = it.activity;
  const has = a.has(g.by.you);
  const name = nameOf(g.head.path);
  const n = g.files;
  const where = place(g.folder, disk);
  let what: string;
  switch (g.op) {
    case "created":
      what = `${a.created(has, name)} ${a.inFolder(where)}`;
      break;
    case "added":
      what = `${a.added(has, n, name)} ${a.inFolder(where)}`;
      break;
    case "written":
      what = `${a.written(has, n, name)} ${a.inFolder(where)}`;
      break;
    case "replaced":
      what = `${a.replaced(has, n, name)} ${a.inFolder(where)}`;
      break;
    case "deleted":
      what =
        g.kind === "folder" ? `${a.deletedFolder(has, name)} ${a.fromFolder(where)}` : `${a.deleted(has, n, name)} ${a.fromFolder(where)}`;
      break;
    case "renamed": {
      const to = g.head.to ?? "";
      what =
        parentOf(to) === g.folder ? a.renamed(has, name, nameOf(to)) : a.moved(has, name, place(parentOf(to), disk));
      break;
    }
  }
  return { who: who(g.by), what };
}

/** The line under a row: how many files and how big, or where the old version went. */
export function meta(g: Group): string | null {
  if (g.op === "replaced") return it.activity.previousInTrash;
  if (g.kind === "folder" && g.op === "deleted") return it.activity.files(g.files, g.bytes ? bytes(g.bytes) : null);
  if (g.files > 1 || (g.files === 1 && g.bytes > 0 && g.op !== "deleted")) return it.activity.files(g.files, g.bytes ? bytes(g.bytes) : null);
  if (g.op === "deleted" && g.bytes > 0) return bytes(g.bytes);
  return null;
}

/** The files a row lists when expanded: the events other than a folder's own. */
export function listed(g: Group): FeedEvent[] {
  return g.events.filter((e) => e.kind === "file");
}

/** What a restore of a deleted row puts back: the folder, or each file. */
export function restoreItems(g: Group): { path: string; at: number }[] {
  if (g.kind === "folder") return [{ path: g.head.path, at: g.head.at }];
  return g.events.map((e) => ({ path: e.path, at: e.at }));
}

/** "512 MB finora, circa 8 MB/s" for a file being written. */
export function progress(l: LiveFile): string | null {
  if (l.size == null) return null;
  return it.activity.soFar(bytes(l.size), l.speed ? bytes(l.speed) : null);
}

/** The "Adesso" line for a file being written now. */
export function liveSentence(l: LiveFile, disk: string): { who: string; what: string; detail: string | null } {
  const name = nameOf(l.path);
  const where = place(parentOf(l.path), disk);
  const verb = l.op === "added" ? it.activity.copying : it.activity.writing;
  return {
    who: who(l.by),
    what: `${verb("", l.by.you, name).trim()} ${it.activity.inFolder(where)}`,
    detail: l.op === "added" ? progress(l) : null,
  };
}

/** What colleagues are doing in one project folder of a disk, for its row in the panel. */
export function projectLive(live: LiveFile[], disk: string, project: string): { kind: "arriving" | "in_use"; line: string } | null {
  const mine = live.filter((l) => !l.this_mac && l.disk === disk && under(l.path, project));
  const arriving = mine.filter((l) => l.op === "added");
  if (arriving.length > 0) {
    const first = arriving[0]!;
    const by = who(first.by);
    const line =
      arriving.length === 1 ? it.live.from(by, progress(first) ?? nameOf(first.path)) : it.live.files(arriving.length, by);
    return { kind: "arriving", line };
  }
  const writing = mine[0];
  if (writing) return { kind: "in_use", line: it.live.writing(who(writing.by), nameOf(writing.path)) };
  return null;
}

/** How many colleagues' files are arriving on a disk, and from whom, for its row. */
export function diskArrivals(live: LiveFile[], disk: string): string | null {
  const arriving = live.filter((l) => !l.this_mac && l.disk === disk && l.op === "added");
  if (arriving.length === 0) return null;
  return it.live.disk(arriving.length, who(arriving[0]!.by));
}
