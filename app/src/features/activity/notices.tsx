/**
 * The activity's notices in the panel and its notifications (concept-f 4C,
 * DECISIONS #27): a notification informs and lasts seconds, the undo lives in
 * the panel until it is done or closed. Undoing a colleague's rename asks
 * first, since it unlinks their projects again if they relinked them.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { Button, Confirm, Notice } from "@/components/ui";
import { nameOf, parentOf, who } from "@/features/activity/activity";
import { useActivitySnapshot } from "@/features/activity/hooks";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { bytes } from "@/lib/format";
import type { ActivityNotice, Alert, CommandError } from "@/lib/types";

const n = it.activityNotices;

/** A notification's title and body. */
export function alertText(a: Alert): { title: string; body: string } {
  switch (a.kind) {
    case "mass_delete":
      return it.alerts.massDelete(a.name, a.files, a.folder ?? it.trash.thisDisk);
    case "renamed":
      return a.by.you ? it.alerts.renamedMine(a.from, a.to) : it.alerts.renamed(who(a.by), a.from, a.to);
    case "renamed_back":
      return it.alerts.renamedBack(a.by, a.from, a.to);
    case "arriving":
      return it.alerts.arriving(a.by, a.file, a.folder, a.size != null ? bytes(a.size) : null);
    case "arrived":
      return it.alerts.arrived(a.by, a.folder);
  }
}

const SOFT = "bg-raised shadow-[0_0_0_0.5px_rgba(0,0,0,0.12)]";

/** Done is green, failed red; a colleague's rename is happening to you; the rest is a note. */
function led(x: ActivityNotice): "ok" | "err" | "dl" | undefined {
  if (x.state === "done") return "ok";
  if (x.state === "failed") return "err";
  return x.kind === "renamed" && !x.by.you ? "dl" : undefined;
}

export function ActivityNotices() {
  const notices = useActivitySnapshot().data?.notices ?? [];
  if (notices.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 px-4 pb-3">
      {notices.map((x) => (
        <Item key={x.id} notice={x} />
      ))}
    </div>
  );
}

function refusalText(x: ActivityNotice): string {
  const e = x.error ?? "";
  if (e === "taken") return it.activity.refusal.taken(x.kind === "renamed" ? nameOf(x.path) : "");
  if (e === "gone" || e === "no_folder" || e === "not_in_trash") return it.activity.refusal[e];
  return `${n.failed} ${e}`;
}

function Item({ notice: x }: { notice: ActivityNotice }) {
  const [confirming, setConfirming] = useState(false);
  const undo = useMutation<void, CommandError>({ mutationFn: () => backend.undoNotice(x.id) });
  const dismiss = useMutation<void, CommandError>({ mutationFn: () => backend.dismissNotice(x.id) });
  const reveal = useMutation<void, CommandError, string>({ mutationFn: backend.revealInFinder });
  const disk = x.folder ?? it.trash.thisDisk;
  const working = x.state === "working" || undo.isPending;
  const close = (label: string) => (
    <Button variant="ghost" size="sm" className="-ml-2.5" disabled={working} onClick={() => dismiss.mutate()}>
      {label}
    </Button>
  );
  const finder = x.state === "done" && x.finder_path && (
    <Button size="sm" className={SOFT} onClick={() => reveal.mutate(x.finder_path!)}>
      {n.showInFinder}
    </Button>
  );
  const undoButton = (label: string, ask: boolean) =>
    x.can_undo &&
    x.state !== "done" && (
      <Button size="sm" className={SOFT} disabled={working} onClick={() => (ask ? setConfirming(true) : undo.mutate())}>
        <RotateCcw size={14} strokeWidth={1.7} aria-hidden="true" />
        {working ? n.working : label}
      </Button>
    );

  let title: string;
  let body: string;
  let actions;
  switch (x.kind) {
    case "mass_delete":
      if (x.state === "done") {
        title = n.massDelete.done(x.name, x.files);
        body = "";
        actions = (
          <>
            {finder}
            {close(n.close)}
          </>
        );
      } else {
        title = n.massDelete.title(x.name, x.files, disk);
        body = `${n.massDelete.body(x.name, x.files)}${x.bytes ? ` (${bytes(x.bytes)})` : ""}`;
        actions = (
          <>
            {undoButton(n.massDelete.undo(x.name, x.files), false)}
            {close(n.fine)}
          </>
        );
      }
      break;
    case "renamed": {
      const name = nameOf(x.path);
      const moved = parentOf(x.to) !== parentOf(x.path);
      const where = parentOf(x.to) || disk;
      if (x.state === "done") {
        title = n.renamed.done;
        body = n.renamed.doneBody(name, x.by.you ? null : who(x.by));
        actions = (
          <>
            {finder}
            {close(n.close)}
          </>
        );
      } else if (x.by.you) {
        title = moved ? n.renamed.mineMovedTitle(name, where) : n.renamed.mineTitle(name, nameOf(x.to));
        body = n.renamed.mineBody;
        actions = (
          <>
            {undoButton(n.renamed.undoMine, false)}
            {close(n.fine)}
          </>
        );
      } else {
        title = moved ? n.renamed.movedTitle(who(x.by), name) : n.renamed.title(who(x.by), name);
        body = moved ? n.renamed.movedBody(where) : n.renamed.body(nameOf(x.to));
        actions = (
          <>
            {undoButton(n.renamed.undo, true)}
            {close(n.renamed.ok)}
          </>
        );
      }
      break;
    }
    case "renamed_back":
      title = n.renamedBack.title(who(x.by));
      body = n.renamedBack.body(nameOf(x.to), nameOf(x.path));
      actions = close(n.renamed.ok);
      break;
  }

  if (confirming && x.kind === "renamed") {
    return (
      <Confirm
        title={it.activity.confirm.title}
        body={it.activity.confirm.body(who(x.by), nameOf(x.path))}
        confirm={it.activity.confirm.confirm}
        cancel={it.activity.confirm.cancel}
        onConfirm={() => {
          setConfirming(false);
          undo.mutate();
        }}
        onCancel={() => setConfirming(false)}
      />
    );
  }
  const failure = undo.error ?? dismiss.error ?? reveal.error;
  return (
    <Notice led={led(x)} title={title}>
      {body && <span className="block">{body}</span>}
      {x.state === "failed" && <span className="mt-1 block text-ink-2">{refusalText(x)}</span>}
      {failure && <span className="mt-1 block text-ink-2">{it.errors[failure.code] ?? failure.message}</span>}
      <span className="mt-2 flex flex-wrap items-center gap-2">{actions}</span>
    </Notice>
  );
}
