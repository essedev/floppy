/**
 * The disk window's Activity tab (concept-f-trash-activity 2A, DECISIONS
 * #27): who added, renamed, moved and deleted what, by day, filtered by
 * person and type; what is being written now on top; and the remedy on the
 * row itself, a restore or a rename undone.
 */

import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, RefreshCw, RotateCcw } from "lucide-react";
import { Button, Confirm, Led, Notice, SlotMark } from "@/components/ui";
import {
  type Group,
  group,
  listed,
  liveSentence,
  matches,
  meta,
  nameOf,
  people,
  restoreItems,
  sentence,
  type TypeFilter,
} from "@/features/activity/activity";
import { activityKeys, useActivitySnapshot } from "@/features/activity/hooks";
import type { WindowDisk } from "@/features/activity/DiskWindow";
import { dayLabel, timeOf } from "@/features/trash/trash";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { bytes } from "@/lib/format";
import type { CommandError, RenameRefusal } from "@/lib/types";

/** How often the open tab reads the disk's history again. */
const REFRESH_MS = 15_000;
/** Files listed under an expanded row before "e altri N". */
const LISTED = 12;

interface ActivityTabProps {
  disk: WindowDisk;
  /** Opens the trash tab searching for a name. */
  onTrash: (name: string) => void;
}

export function ActivityTab({ disk, onTrash }: ActivityTabProps) {
  const feed = useInfiniteQuery({
    queryKey: activityKeys.feed(disk.disk),
    queryFn: ({ pageParam }) => backend.getActivity(disk.disk, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.before,
    refetchInterval: REFRESH_MS,
    refetchOnWindowFocus: true,
    retry: false,
  });
  const snapshot = useActivitySnapshot();
  const [person, setPerson] = useState("all");
  const [type, setType] = useState<TypeFilter>("all");
  const pages = feed.data?.pages ?? [];
  const groups = useMemo(() => group(pages.flatMap((p) => p.events)), [pages]);
  const live = (snapshot.data?.live ?? []).filter((l) => l.disk === disk.disk);
  const first = pages[0];

  if (feed.isPending) return <p className="m-0 p-6 text-[13px] text-ink-2">{it.activity.loading}</p>;
  if (feed.isError) {
    const e = feed.error as unknown as CommandError;
    return (
      <div className="flex flex-col items-start gap-3 p-6">
        <Notice led="err">{it.errors[e.code] ?? e.message}</Notice>
        <Button onClick={() => void feed.refetch()}>{it.activity.retry}</Button>
      </div>
    );
  }

  const shown = groups.filter((g) => matches(g, person, type));
  const days: { label: string; items: Group[] }[] = [];
  const now = Date.now() / 1000;
  for (const g of shown) {
    const label = dayLabel(g.at, now);
    const last = days[days.length - 1];
    if (last?.label === label) last.items.push(g);
    else days.push({ label, items: [g] });
  }
  const who = [{ key: "all", label: it.activity.everyone }, ...people(groups)];
  const types: TypeFilter[] = ["all", "add", "ren", "del"];

  return (
    <div className="flex flex-col gap-4 px-6 pt-4 pb-6">
      <div className="flex flex-wrap items-center gap-2.5">
        <Segments label={it.activity.peopleLabel} items={who} value={person} onChange={setPerson} />
        <Segments
          label={it.activity.typeLabel}
          items={types.map((t) => ({ key: t, label: it.activity.types[t] }))}
          value={type}
          onChange={(t) => setType(t as TypeFilter)}
        />
        <button
          type="button"
          className="ml-auto grid size-[30px] place-items-center rounded-lg border-0 bg-transparent text-ink-2 hover:bg-sunken hover:text-ink disabled:opacity-45"
          aria-label={it.activity.refresh}
          title={it.activity.refresh}
          disabled={feed.isFetching}
          onClick={() => void feed.refetch()}
        >
          <RefreshCw size={15} strokeWidth={1.7} aria-hidden="true" className={feed.isFetching ? "animate-spin" : ""} />
        </button>
      </div>

      {first?.offline_since_ms != null && <Notice led="err">{it.activity.offline(timeOf(first.offline_since_ms / 1000))}</Notice>}

      {live.length > 0 && (
        <section aria-label={it.activity.now} className="flex flex-col gap-1.5 rounded-xl bg-sunken px-3.5 py-2.5">
          {live.map((l) => {
            const s = liveSentence(l, disk.name);
            return (
              <div key={`${l.by.name}:${l.path}`} className="flex items-start gap-2.5 text-[13px] leading-[1.4]">
                <span className="mt-[5px] flex">
                  <Led state="dl" blink />
                </span>
                <span className="min-w-0 flex-1">
                  <b className="font-semibold">{s.who}</b> {s.what}
                  {s.detail && <span className="text-ink-2"> · {s.detail}</span>}
                </span>
                <span className="shrink-0 pt-px font-ro text-[11px] text-ink-3 uppercase">{it.activity.now}</span>
              </div>
            );
          })}
        </section>
      )}

      {groups.length === 0 ? (
        <div className="flex flex-col items-center gap-1.5 py-14 text-center">
          <b className="text-[14px]">{it.activity.empty.title(disk.name)}</b>
          <p className="m-0 max-w-[420px] text-[13px] text-ink-2">{it.activity.empty.body}</p>
        </div>
      ) : shown.length === 0 ? (
        <div className="flex flex-col items-center gap-1.5 py-14 text-center">
          <b className="text-[14px]">{it.activity.noMatch.title}</b>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setPerson("all");
              setType("all");
            }}
          >
            {it.activity.noMatch.clear}
          </Button>
        </div>
      ) : (
        days.map((d) => (
          <section key={d.label}>
            <b className="mb-1.5 ml-1 block text-[13px] font-semibold">{d.label}</b>
            <div className="well rounded-xl bg-raised [&>*+*]:border-t [&>*+*]:border-line">
              {d.items.map((g) => (
                <Row key={g.id} group={g} disk={disk} onTrash={onTrash} />
              ))}
            </div>
          </section>
        ))
      )}

      {feed.hasNextPage && (
        <Button className="self-center" disabled={feed.isFetchingNextPage} onClick={() => void feed.fetchNextPage()}>
          {it.activity.more}
        </Button>
      )}

      <p className="m-0 flex items-start text-[12.5px] leading-[1.45] text-ink-2">
        <span className="mt-[6px] flex shrink-0">
          <SlotMark />
        </span>
        <span>{first && !first.shared && first.offline_since_ms == null ? `${it.activity.local} ${it.activity.foot}` : it.activity.foot}</span>
      </p>
    </div>
  );
}

function Segments({
  label,
  items,
  value,
  onChange,
}: {
  label: string;
  items: { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex rounded-[10px] bg-sunken p-0.5">
      {items.map((i) => (
        <button
          key={i.key}
          type="button"
          aria-pressed={value === i.key}
          onClick={() => onChange(i.key)}
          className={`h-8 rounded-lg border-0 px-3 text-[13px] whitespace-nowrap ${value === i.key ? "bg-raised font-semibold text-ink" : "bg-transparent text-ink-2"}`}
        >
          {i.label}
        </button>
      ))}
    </div>
  );
}

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]!.toLocaleUpperCase("it"))
      .join("") || "?"
  );
}

type Done = { line: string; finder: string | null };

function Row({ group: g, disk, onTrash }: { group: Group; disk: WindowDisk; onTrash: (name: string) => void }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState<Done | null>(null);
  const restore = useMutation<Done, CommandError>({
    mutationFn: async () => {
      const out = await backend.restoreDeleted(disk.disk, restoreItems(g));
      if (out.restored.length === 0) throw { code: "not_in_trash", message: out.failed[0]?.error ?? "" } satisfies CommandError;
      return { line: it.activity.restored(out.restored.length), finder: out.restored[0]?.finder_path ?? null };
    },
    onSuccess: setDone,
  });
  const undo = useMutation<RenameRefusal | null, CommandError>({
    mutationFn: () => backend.renameBack(disk.disk, g.head.path, g.head.to ?? ""),
    onSuccess: (refusal) => {
      if (!refusal) setDone({ line: it.activity.renamedBack, finder: null });
    },
  });
  const reveal = useMutation<void, CommandError, string>({ mutationFn: backend.revealInFinder });
  const s = sentence(g, disk.name);
  const m = meta(g);
  const files = listed(g);
  const expandable = files.length > 1 || (g.kind === "folder" && files.length > 0);
  const refusal = undo.data;

  let action = null;
  if (done) {
    action = done.finder && (
      <Button size="sm" disabled={reveal.isPending} onClick={() => reveal.mutate(done.finder!)}>
        {it.activity.showInFinder}
      </Button>
    );
  } else if (g.op === "deleted" && disk.writable) {
    action = (
      <Button size="sm" disabled={restore.isPending} onClick={() => restore.mutate()}>
        <RotateCcw size={14} strokeWidth={1.7} aria-hidden="true" />
        {restore.isPending
          ? it.activity.restoring
          : g.kind === "folder"
            ? it.activity.restoreFolder
            : g.files > 1
              ? it.activity.restoreFiles(g.files)
              : it.activity.restore}
      </Button>
    );
  } else if (g.op === "replaced") {
    action = (
      <Button variant="ghost" size="sm" onClick={() => onTrash(nameOf(g.head.path))}>
        {it.activity.inTrash}
      </Button>
    );
  } else if (g.op === "renamed" && g.kind === "folder" && disk.writable && g.head.to) {
    action = (
      <Button size="sm" disabled={undo.isPending} onClick={() => (g.by.you ? undo.mutate() : setConfirming(true))}>
        <RotateCcw size={14} strokeWidth={1.7} aria-hidden="true" />
        {undo.isPending ? it.activity.undoing : it.activity.undoRename}
      </Button>
    );
  }

  const failure = restore.error ?? undo.error ?? reveal.error;
  return (
    <div className="px-3.5 py-2.5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <time className="w-[40px] shrink-0 pt-px font-ro text-[11.5px] text-ink-2">{timeOf(g.at)}</time>
        <span
          className="grid size-[22px] shrink-0 place-items-center rounded-full bg-sunken text-[10px] font-semibold text-ink-2"
          aria-hidden="true"
        >
          {initials(s.who)}
        </span>
        <div className="min-w-[220px] flex-1 basis-[260px] text-[13px] leading-[1.4]">
          <span>
            <b className="font-semibold">{s.who}</b> {s.what}
          </span>
          {m && <small className="block text-[12px] text-ink-2">{m}</small>}
          {expandable && (
            <button
              type="button"
              className="mt-0.5 flex items-center gap-1 border-0 bg-transparent p-0 text-[12px] text-ink-2 hover:text-ink"
              aria-expanded={open}
              onClick={() => setOpen(!open)}
            >
              {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
              {open ? it.activity.hideFiles : it.activity.showFiles}
            </button>
          )}
          {open && (
            <div className="mt-1 flex flex-col gap-0.5 text-[12px] text-ink-2">
              {files.slice(0, LISTED).map((e) => (
                <div key={e.id} className="flex gap-3">
                  <span className="min-w-0 flex-1 truncate">{nameOf(e.path)}</span>
                  {e.size != null && <span className="font-ro text-[11.5px]">{bytes(e.size)}</span>}
                </div>
              ))}
              {files.length > LISTED && <span>{it.activity.andMore(files.length - LISTED)}</span>}
            </div>
          )}
          {done && (
            <small className="mt-0.5 flex items-center gap-1.5 text-[12px] text-ink-2">
              <Led state="ok" />
              {done.line}
            </small>
          )}
        </div>
        {action && <span className="ml-auto flex shrink-0 justify-end">{action}</span>}
      </div>
      {confirming && (
        <Confirm
          className="mt-2"
          title={it.activity.confirm.title}
          body={it.activity.confirm.body(s.who, nameOf(g.head.path))}
          confirm={it.activity.confirm.confirm}
          cancel={it.activity.confirm.cancel}
          onConfirm={() => {
            setConfirming(false);
            undo.mutate();
          }}
          onCancel={() => setConfirming(false)}
        />
      )}
      {refusal && (
        <Notice led="err" className="mt-2">
          {refusal === "taken" ? it.activity.refusal.taken(nameOf(g.head.path)) : it.activity.refusal[refusal]}
        </Notice>
      )}
      {failure && (
        <Notice led="err" className="mt-2">
          {failure.code === "not_in_trash" ? it.activity.refusal.not_in_trash : `${it.activity.failed} ${it.errors[failure.code] ?? failure.message}`}
        </Notice>
      )}
    </div>
  );
}
