/**
 * The trash window (concept-f-trash-activity 1B, DECISIONS #27): the disks this
 * Mac has mounted on the left, the chosen disk's trash by day on the right,
 * with search, deleted and replaced apart, and a restore that never writes
 * over a file. It reads the engine, so it works with the control plane down.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { File, Film, Folder, RefreshCw, RotateCcw, Search } from "lucide-react";
import { Button, Led, Notice, SlotMark } from "@/components/ui";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { bytes } from "@/lib/format";
import type { CommandError, Restored, TrashDisk, TrashEntry } from "@/lib/types";
import {
  byDay,
  diskKey,
  diskName,
  fromLine,
  type KindFilter,
  matches,
  restoreLabel,
  restoredLine,
  timeOf,
} from "@/features/trash/trash";

const trashKey = ["trash"] as const;

export function TrashWindow() {
  const trash = useQuery({ queryKey: trashKey, queryFn: backend.getTrash, refetchOnWindowFocus: true, retry: false });
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => backend.onTrashDisk((d) => setChosen(d ?? null)), []);
  const disks = trash.data?.disks ?? [];
  const current = disks.find((d) => diskKey(d) === chosen) ?? disks[0];

  return (
    <main className="flex h-screen bg-surface text-ink">
      <aside className="flex w-[210px] shrink-0 flex-col gap-1 border-r border-line px-2.5 pt-4 pb-3">
        <h2 className="m-0 mb-1 ml-2 text-[11px] font-semibold tracking-[0.1em] text-ink-3 uppercase">{it.trash.disks}</h2>
        {disks.map((d) => (
          <button
            key={diskKey(d)}
            type="button"
            aria-pressed={d === current}
            onClick={() => setChosen(diskKey(d))}
            className={`flex items-center gap-2 rounded-lg border-0 px-2.5 py-2 text-left text-[13px] ${
              d === current ? "bg-raised font-semibold" : "bg-transparent hover:bg-sunken"
            }`}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate">{diskName(d)}</span>
              {!d.writable && <small className="block text-[11.5px] font-normal text-ink-2">{it.trash.readOnly}</small>}
            </span>
            <span className="font-ro text-[11.5px] text-ink-2">{d.entries.length}</span>
          </button>
        ))}
      </aside>
      <section className="min-w-0 flex-1 overflow-y-auto">
        {trash.isPending ? (
          <p className="m-0 p-6 text-[13px] text-ink-2">{it.trash.loading}</p>
        ) : trash.isError ? (
          <div className="flex flex-col items-start gap-3 p-6">
            <Notice led="err">{errorText(trash.error as unknown as CommandError)}</Notice>
            <Button onClick={() => void trash.refetch()}>{it.trash.retry}</Button>
          </div>
        ) : !current ? (
          <Empty title={it.trash.noDisks.title} body={it.trash.noDisks.body} />
        ) : (
          <DiskTrash key={diskKey(current)} disk={current} refreshing={trash.isFetching} onRefresh={() => void trash.refetch()} />
        )}
      </section>
    </main>
  );
}

function errorText(e: CommandError | null | undefined): string {
  if (!e) return it.errors.io;
  return it.errors[e.code] ?? e.message;
}

interface DiskTrashProps {
  disk: TrashDisk;
  refreshing: boolean;
  onRefresh: () => void;
}

function DiskTrash({ disk, refreshing, onRefresh }: DiskTrashProps) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<KindFilter>("all");
  // What this window restored stays on screen, with its Finder button, after a refresh.
  const [done, setDone] = useState<Record<string, { entry: TrashEntry; restored: Restored }>>({});
  const name = diskName(disk);
  const now = Date.now() / 1000;
  const entries = useMemo(() => {
    const listed = new Set(disk.entries.map((e) => e.id));
    return [...disk.entries, ...Object.values(done).filter((d) => !listed.has(d.entry.id)).map((d) => d.entry)];
  }, [disk.entries, done]);
  const groups = byDay(entries.filter((e) => matches(e, q, kind)), now);
  const total = disk.entries.reduce((n, e) => n + e.size, 0);

  if (entries.length === 0) {
    return (
      <Empty
        title={it.trash.empty.title(name)}
        body={disk.trash_days > 0 ? it.trash.empty.body(name, disk.trash_days) : it.trash.empty.noTrash(name)}
      />
    );
  }
  return (
    <div className="flex flex-col gap-4 px-6 pt-4 pb-6">
      <div className="flex flex-wrap items-center gap-2.5">
        <label className="flex h-9 min-w-[160px] flex-1 items-center gap-2 rounded-[10px] bg-sunken px-3 text-ink-2">
          <Search size={14} strokeWidth={1.7} aria-hidden="true" />
          <input
            className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-ink outline-none placeholder:text-ink-3"
            placeholder={it.trash.search}
            aria-label={it.trash.searchLabel}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <div role="group" aria-label={it.trash.kindLabel} className="flex rounded-[10px] bg-sunken p-0.5">
          {(["all", "deleted", "replaced"] as const).map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={kind === k}
              onClick={() => setKind(k)}
              className={`h-8 rounded-lg border-0 px-3 text-[13px] ${kind === k ? "bg-raised font-semibold text-ink" : "bg-transparent text-ink-2"}`}
            >
              {it.trash.kinds[k]}
            </button>
          ))}
        </div>
        <span className="font-ro text-[11.5px] text-ink-2">{it.trash.count(disk.entries.length, bytes(total))}</span>
        <button
          type="button"
          className="grid size-[30px] place-items-center rounded-lg border-0 bg-transparent text-ink-2 hover:bg-sunken hover:text-ink disabled:opacity-45"
          aria-label={it.trash.refresh}
          title={it.trash.refresh}
          disabled={refreshing}
          onClick={onRefresh}
        >
          <RefreshCw size={15} strokeWidth={1.7} aria-hidden="true" className={refreshing ? "animate-spin" : ""} />
        </button>
      </div>

      {!disk.writable && <Notice>{it.trash.readOnlyNote(name)}</Notice>}

      {groups.length === 0 ? (
        <div className="flex flex-col items-center gap-1.5 py-14 text-center">
          <b className="text-[14px]">{it.trash.noMatch.title}</b>
          <p className="m-0 text-[13px] text-ink-2">{it.trash.noMatch.body(name, q.trim())}</p>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setQ("");
              setKind("all");
            }}
          >
            {it.trash.noMatch.clear}
          </Button>
        </div>
      ) : (
        groups.map((g) => (
          <section key={g.label}>
            <div className="mb-1.5 ml-1 flex items-baseline gap-2.5">
              <b className="text-[13px] font-semibold">{g.label}</b>
              <span className="font-ro text-[11px] text-ink-3 uppercase">{it.trash.left(g.left)}</span>
            </div>
            <div className="well rounded-xl bg-raised [&>*+*]:border-t [&>*+*]:border-line">
              {g.items.map((e) => (
                <Row
                  key={e.id}
                  entry={e}
                  disk={disk}
                  done={done[e.id]?.restored ?? null}
                  onDone={(restored) => setDone((d) => ({ ...d, [e.id]: { entry: e, restored } }))}
                />
              ))}
            </div>
          </section>
        ))
      )}

      <p className="m-0 flex items-start text-[12.5px] leading-[1.45] text-ink-2">
        <span className="mt-[6px] flex shrink-0">
          <SlotMark />
        </span>
        <span>{it.trash.foot(disk.trash_days)}</span>
      </p>
    </div>
  );
}

const VIDEO = /\.(mov|mp4|m4v|mxf|avi|mkv|braw|r3d)$/i;

interface RowProps {
  entry: TrashEntry;
  disk: TrashDisk;
  done: Restored | null;
  onDone: (r: Restored) => void;
}

function Row({ entry, disk, done, onDone }: RowProps) {
  const restore = useMutation<Restored, CommandError>({
    mutationFn: () => backend.restoreTrash(disk.disk, entry.id),
    onSuccess: onDone,
  });
  const reveal = useMutation<void, CommandError, string>({ mutationFn: backend.revealInFinder });
  const Icon = entry.kind === "folder" ? Folder : VIDEO.test(entry.name) ? Film : File;
  const error = restore.error ?? reveal.error;
  return (
    <div className="px-3.5 py-2.5">
      <div className="flex items-center gap-3">
        <Icon size={16} strokeWidth={1.6} aria-hidden="true" className="shrink-0 text-ink-2" />
        <div className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] font-medium">{entry.name}</span>
          {done ? (
            <small className="flex items-center gap-1.5 truncate text-[12px] text-ink-2">
              <Led state="ok" />
              {restoredLine(done)}
            </small>
          ) : (
            <small className="block truncate text-[12px] text-ink-2">{fromLine(entry, diskName(disk))}</small>
          )}
        </div>
        {done ? (
          <span className="flex w-[196px] shrink-0 justify-end">
            {done.finder_path && (
              <Button size="sm" disabled={reveal.isPending} onClick={() => reveal.mutate(done.finder_path!)}>
                {it.trash.showInFinder}
              </Button>
            )}
          </span>
        ) : (
          <>
            <span
              className={`w-[92px] shrink-0 truncate text-[12.5px] ${entry.by_this_mac ? "text-ink" : "text-ink-3"}`}
              title={entry.by_this_mac ? undefined : it.trash.unknownTitle}
            >
              {entry.by_this_mac ? it.trash.you : it.trash.unknown}
            </span>
            <span className="w-[44px] shrink-0 font-ro text-[11.5px] text-ink-2">{timeOf(entry.deleted_at)}</span>
            <span className="w-[64px] shrink-0 text-right font-ro text-[11.5px] text-ink-2">{bytes(entry.size)}</span>
            {disk.writable && (
              <span className="flex w-[196px] shrink-0 justify-end">
                <Button size="sm" disabled={restore.isPending} onClick={() => restore.mutate()}>
                  <RotateCcw size={14} strokeWidth={1.7} aria-hidden="true" />
                  {restore.isPending ? it.trash.restoring : restoreLabel(entry)}
                </Button>
              </span>
            )}
          </>
        )}
      </div>
      {error && (
        <Notice led="err" className="mt-2">
          {error.message.includes("not in the trash") ? it.trash.notInTrash : `${it.trash.failed} ${errorText(error)}`}
        </Notice>
      )}
    </div>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1.5 px-10 text-center">
      <b className="text-[15px]">{title}</b>
      <p className="m-0 max-w-[420px] text-[13px] leading-[1.45] text-ink-2">{body}</p>
    </div>
  );
}
