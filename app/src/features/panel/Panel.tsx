/**
 * The panel under the menu bar icon (concept-c-app, DECISIONS #12): the
 * faceplate first, then the state in one sentence with one large action, the
 * space on this Mac, the projects with their row menu, and the footer. In the
 * team version the rows are the team's disks (DECISIONS #20: folders of the
 * one share), each with its state on its LED and its projects inside.
 */

import { type ReactNode, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Ellipsis, ExternalLink, Folder as FolderIcon, History, Power, RefreshCw, Settings, Trash2 } from "lucide-react";
import { Faceplate } from "@/components/Faceplate";
import { Button, Confirm, Led, Notice, Readout, SlotMark } from "@/components/ui";
import { useAuth } from "@/features/account/hooks";
import { diskArrivals, projectLive } from "@/features/activity/activity";
import { useActivitySnapshot } from "@/features/activity/hooks";
import { ActivityNotices, alertText } from "@/features/activity/notices";
import { lostAccess } from "@/features/account/view";
import { faceFor, isBusy } from "@/features/disk/face";
import { keys, useAppInfo, useCountdown, useDiskAction, useFolders, useStatus } from "@/features/disk/hooks";
import { diskLook, noDisksCase, teamOf } from "@/features/disk/team";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { gb, gbUnit } from "@/lib/format";
import type { CommandError, DiskStatus, DiskView, Folder, LiveFile, TeamView } from "@/lib/types";

const WITH_SPACE = new Set(["connected", "unreachable", "starting", "stopping"]);
const WITH_DISKS = new Set(["connected", "unreachable", "starting", "no_disks"]);

export function Panel() {
  const { data: status } = useStatus();
  const info = useAppInfo();
  const qc = useQueryClient();
  const root = useRef<HTMLDivElement>(null);
  const team = status ? teamOf(status) : null;

  // The window takes the panel's height: its content changes with the state.
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const ro = new ResizeObserver(() => void backend.setHeight(el.offsetHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(
    () =>
      backend.onPanelOpened(() => {
        void backend.refresh();
        void qc.invalidateQueries({ queryKey: keys.folders });
      }),
    [qc],
  );

  // Notifications from the native side, in the dictionary's words.
  useEffect(
    () =>
      backend.onNotice((n) => {
        const text = n.kind === "reconnected" ? it.notifications.reconnected : it.notifications.warmupDone(n.name);
        void backend.notify(text.title, text.body);
      }),
    [],
  );
  useEffect(
    () =>
      backend.onAlert((a) => {
        const text = alertText(a);
        void backend.notify(text.title, text.body);
      }),
    [],
  );

  return (
    <div ref={root} className="panel flex w-[352px] flex-col overflow-hidden rounded-[20px] bg-surface" aria-label={it.panel.label}>
      {status ? (
        <>
          <div className="mx-2 mt-2">
            <Faceplate {...faceFor(status)} />
          </div>
          <StatusSection status={status} />
          <ActivityNotices />
          <RevokedSection />
          {WITH_SPACE.has(status.state) && status.space?.cache_used_mb != null && <SpaceSection status={status} />}
          {team && !team.refused_single ? (
            WITH_DISKS.has(status.state) && team.disks.length > 0 && <DiskList status={status} team={team} />
          ) : (
            status.state === "connected" && <FolderList status={status} />
          )}
          <Footer status={status} version={info.data?.version} />
        </>
      ) : (
        <>
          <div className="mx-2 mt-2">
            <Faceplate led="off" readout={it.face.starting} fill={0} />
          </div>
          <p className="px-5 py-5 text-ink-2">{it.panel.checking}</p>
        </>
      )}
    </div>
  );
}

/** An admin took this Mac off the team: say so here too, with the way back. */
function RevokedSection() {
  const { data: auth } = useAuth();
  const lost = auth?.phase === "signed_out" ? lostAccess(auth.notice) : null;
  if (!lost) return null;
  return (
    <div className="px-4 pb-3">
      <Notice led="err" title={lost.title}>
        {lost.body}
        <span className="mt-2.5 block">
          <Button size="sm" variant="primary" onClick={() => void backend.openSettings()}>
            {it.account.revoked.signIn}
          </Button>
        </span>
      </Notice>
    </div>
  );
}

function ActionError({ error }: { error: CommandError | null }) {
  if (!error) return null;
  return <Notice led="err">{it.errors[error.code] ?? error.message}</Notice>;
}

function StatusSection({ status }: { status: DiskStatus }) {
  const seconds = useCountdown(status);
  const { data: auth } = useAuth();
  const team = teamOf(status);
  const start = useDiskAction(backend.start);
  const restart = useDiskAction(backend.restart);
  const install = useDiskAction(backend.installEngine);
  const cancel = useDiskAction(backend.cancel);
  const openFinder = useDiskAction<string | undefined>(backend.openInFinder);
  const freeAll = useDiskAction<string>(backend.free);
  const [confirmFree, setConfirmFree] = useState(false);
  const [confirmRestart, setConfirmRestart] = useState(false);

  let title: string;
  let body: string | null = null;
  const extra: ReactNode[] = [];
  let actions: ReactNode;

  const finder = (
    <Button variant="primary" size="lg" className="flex-1" disabled={!status.mount_point} onClick={() => openFinder.mutate(undefined)}>
      <ExternalLink size={16} strokeWidth={1.7} aria-hidden="true" />
      {it.panel.openFinder}
    </Button>
  );

  switch (status.state) {
    case "connected": {
      const job = status.job;
      if (isBusy(status) && job) {
        const name = job.name ?? "";
        title = job.kind === "warmup" ? it.panel.activity.warmup(name) : it.panel.activity.free(name);
        body = job.kind === "warmup" ? it.panel.activity.warmupBody : it.panel.activity.freeBody;
        actions = (
          <>
            {finder}
            <Button variant="ghost" size="lg" onClick={() => cancel.mutate()}>
              {it.panel.stop}
            </Button>
          </>
        );
      } else {
        const failing = team?.failing ?? 0;
        const copy = status.mode === "team" ? it.panel.connectedTeam : it.panel.connected;
        title = failing > 0 ? it.panel.failing.title(failing) : copy.title;
        body = failing > 0 ? it.panel.failing.body : copy.body;
        actions = finder;
      }
      break;
    }
    case "no_disks": {
      const which = noDisksCase(team, auth);
      title = it.panel.noDisks[which].title;
      body = it.panel.noDisks[which].body;
      actions =
        which === "signed_out" ? (
          <Button variant="primary" size="lg" className="flex-1" onClick={() => void backend.openSettings()}>
            {it.panel.noDisks.signIn}
          </Button>
        ) : null;
      break;
    }
    case "starting":
      title = it.panel.starting.title;
      body = it.panel.starting.body;
      actions = finder;
      break;
    case "stopping":
      title = it.panel.stopping.title;
      body = it.panel.stopping.body;
      actions = finder;
      break;
    case "unreachable":
      title = it.panel.unreachable.title;
      body = it.panel.unreachable.body(seconds);
      extra.push(
        <Notice key="note">
          {it.panel.unreachable.note}
          {status.problem && (
            <span className="mt-1.5 block">
              <Readout>{it.problem[status.problem]}</Readout>
            </span>
          )}
          {status.engine_error && <span className="mt-1 block font-ro text-[11.5px] text-ink-2">{status.engine_error}</span>}
        </Notice>,
      );
      actions = (
        <Button variant="primary" size="lg" className="flex-1" disabled={start.isPending} onClick={() => start.mutate()}>
          <RefreshCw size={16} strokeWidth={1.7} aria-hidden="true" />
          {it.panel.retryNow}
        </Button>
      );
      break;
    case "off":
      title = it.panel.off.title;
      body = it.panel.off.body;
      actions = (
        <Button variant="primary" size="lg" className="flex-1" disabled={start.isPending} onClick={() => start.mutate()}>
          <Power size={16} strokeWidth={1.7} aria-hidden="true" />
          {it.panel.turnOn}
        </Button>
      );
      break;
    case "needs_setup":
    case "not_installed": {
      const copy =
        status.state === "needs_setup"
          ? it.panel.needsSetup
          : status.install === "fresh"
            ? it.panel.notInstalled
            : status.bundled
              ? it.panel.notConfigured
              : it.panel.noBundle;
      title = copy.title;
      body = copy.body;
      actions = (
        <Button variant="primary" size="lg" className="flex-1" onClick={() => void backend.openWelcome()}>
          {it.panel.openSetup}
        </Button>
      );
      break;
    }
  }

  // An engine update restarts the disk (`floppy setup`), so it waits for the
  // user: the app never remounts by itself.
  if (status.install === "update" && !["not_installed", "needs_setup", "starting", "stopping"].includes(status.state)) {
    extra.push(
      <Notice key="update" led="dl" title={it.panel.update.title}>
        <span className="block">{it.panel.update.body}</span>
        <Button size="sm" className="mt-2 bg-raised shadow-[0_0_0_0.5px_rgba(0,0,0,0.12)]" disabled={!!status.job || install.isPending} onClick={() => install.mutate()}>
          {it.panel.update.action}
        </Button>
      </Notice>,
    );
  }
  if (team?.refused_single) {
    extra.push(
      <Notice key="single" led="err" title={it.team.single.title}>
        {it.team.single.body}
      </Notice>,
    );
  } else if (team?.error && !team.applying) {
    extra.push(
      <Notice key="team-error" led="err" title={it.team.error.title}>
        <span className="block">{it.team.error.body}</span>
        <span className="mt-1 block font-ro text-[11.5px] text-ink-2">{team.error}</span>
      </Notice>,
    );
  }
  for (const r of team?.rejected ?? []) {
    extra.push(<Notice key={`rejected-${r.id}`}>{it.team.rejected(r.name)}</Notice>);
  }
  // The new memory comes only with a restart, and the app never restarts the
  // disk by itself: the person picks the moment.
  if (team?.restart_needed && !status.job && ["connected", "no_disks", "unreachable"].includes(status.state)) {
    extra.push(
      confirmRestart ? (
        <Confirm
          key="restart"
          title={it.team.restart.confirmTitle}
          body={it.team.restart.confirmBody}
          confirm={it.team.restart.confirm}
          cancel={it.panel.cancel}
          onConfirm={() => {
            setConfirmRestart(false);
            restart.mutate();
          }}
          onCancel={() => setConfirmRestart(false)}
        />
      ) : (
        <Notice key="restart" led="dl" title={it.team.restart.title}>
          <span className="block">{it.team.restart.body}</span>
          <Button size="sm" className="mt-2 bg-raised shadow-[0_0_0_0.5px_rgba(0,0,0,0.12)]" onClick={() => setConfirmRestart(true)}>
            {it.team.restart.action}
          </Button>
        </Notice>
      ),
    );
  }
  if (status.state === "connected" && status.wrong_mount_point && status.mount_point) {
    extra.push(
      <Notice key="mount" led="err" title={it.notices.wrongMount.title(status.mount_point)}>
        {it.notices.wrongMount.body}
      </Notice>,
    );
  }
  const space = status.space;
  if (status.state === "connected" && space?.low && space.mac_free_mb != null) {
    extra.push(
      confirmFree && status.mount_point ? (
        <Confirm
          key="low"
          title={it.notices.lowSpace.confirmTitle}
          body={it.notices.lowSpace.confirmBody}
          confirm={it.folders.freeConfirm.confirm}
          cancel={it.panel.cancel}
          onConfirm={() => {
            setConfirmFree(false);
            freeAll.mutate(status.mount_point!);
          }}
          onCancel={() => setConfirmFree(false)}
        />
      ) : (
        <Notice key="low" led="err" title={it.notices.lowSpace.title}>
          {it.notices.lowSpace.body(gbUnit(space.mac_free_mb), gbUnit(space.reserve_mb / 2))}
          <Button size="sm" className="mt-2 bg-raised shadow-[0_0_0_0.5px_rgba(0,0,0,0.12)]" disabled={!!status.job} onClick={() => setConfirmFree(true)}>
            {it.notices.lowSpace.action}
          </Button>
        </Notice>
      ),
    );
  }
  if (status.last_error) {
    const e = status.last_error;
    extra.push(
      <Notice key="err" led="err" title={it.notices.jobFailed.title(e.kind, e.name)}>
        <span className="block font-ro text-[11.5px] text-ink-2">{e.message}</span>
        <Button size="sm" className="mt-2 bg-raised shadow-[0_0_0_0.5px_rgba(0,0,0,0.12)]" onClick={() => void backend.dismissError()}>
          {it.panel.dismiss}
        </Button>
      </Notice>,
    );
  }

  const failure = start.error ?? restart.error ?? install.error ?? cancel.error ?? openFinder.error ?? freeAll.error ?? null;
  return (
    <section className="flex flex-col gap-4 px-5 pt-5 pb-[22px]">
      <div>
        <h1 className="m-0 text-[20px] leading-[1.2] font-bold tracking-[-0.02em]">{title}</h1>
        {body && <p className="mt-1 mb-0 text-[13.5px] text-ink-2">{body}</p>}
      </div>
      {extra}
      {actions && <div className="flex gap-2">{actions}</div>}
      <ActionError error={failure} />
    </section>
  );
}

function SpaceSection({ status }: { status: DiskStatus }) {
  const s = status.space!;
  const used = s.cache_used_mb;
  const limit = s.cache_limit_mb;
  return (
    <section className="px-5 pb-[18px]">
      <div className="mb-3 flex items-baseline justify-between gap-2 text-[11px] font-semibold tracking-[0.1em] text-ink-3 uppercase">
        <span>{it.space.heading}</span>
        {used != null && limit != null && (
          <b className="font-ro text-[12px] font-normal tracking-[0.02em] text-ink normal-case">{it.space.of(gb(used), gb(limit))}</b>
        )}
      </div>
      {used != null && limit != null ? (
        <ul className="m-0 grid list-none gap-1.5 p-0">
          <LegendRow color="var(--ink)" label={it.space.local} value={gbUnit(used)} />
          <LegendRow color="var(--track)" label={it.space.free} value={gbUnit(Math.max(0, limit - used))} />
          {s.mac_free_mb != null && <LegendRow color="var(--idle)" label={it.space.macFree} value={gbUnit(s.mac_free_mb)} />}
        </ul>
      ) : (
        <p className="m-0 text-[12.5px] text-ink-2">{it.space.unknown}</p>
      )}
    </section>
  );
}

function LegendRow({ color, label, value }: { color: string; label: string; value: string }) {
  return (
    <li className="flex items-center text-[12.5px]">
      <SlotMark color={color} />
      <span className="min-w-0 flex-1 truncate text-ink-2">{label}</span>
      <span className="font-ro text-[12px] text-ink">{value}</span>
    </li>
  );
}

/** The projects at the top of the disk, or inside one team disk (`path`, nested in its row). */
function FolderList({ status, path, disk }: { status: DiskStatus; path?: string; disk?: string }) {
  const folders = useFolders(status.state === "connected" && !!status.mount_point, path);
  const live = useActivitySnapshot().data?.live ?? [];
  const [open, setOpen] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const warmup = useDiskAction<string>(backend.warmup);
  const free = useDiskAction<string>(backend.free);
  const reveal = useDiskAction<string | undefined>(backend.openInFinder);
  const busy = !!status.job;
  const s = status.space;
  const nearlyFull = !!s && s.cache_used_mb != null && s.cache_limit_mb != null && s.cache_used_mb >= s.cache_limit_mb * 0.9;

  const list = folders.data ?? [];
  let content: ReactNode;
  if (folders.isPending) {
    content = <p className="m-0 px-3 py-2 text-[13px] text-ink-2">{it.folders.loading}</p>;
  } else if (folders.isError) {
    content = (
      <div className="flex items-center justify-between gap-2 px-3 py-2 text-[13px] text-ink-2">
        <span>{it.folders.timeout}</span>
        <Button size="sm" onClick={() => void folders.refetch()}>
          {it.folders.retry}
        </Button>
      </div>
    );
  } else if (list.length === 0) {
    content = <p className="m-0 px-3 py-2 text-[13px] text-ink-2">{path ? it.disks.empty : it.folders.empty}</p>;
  } else {
    content = list.map((f) => (
      <FolderRow
        key={f.path}
        folder={f}
        live={disk ? projectLive(live, disk, f.name) : null}
        working={status.job?.path === f.path}
        open={open === f.path}
        confirming={confirm === f.path}
        busy={busy}
        nearlyFull={nearlyFull}
        onToggle={() => {
          setConfirm(null);
          setOpen(open === f.path ? null : f.path);
        }}
        onWarmup={() => {
          setOpen(null);
          warmup.mutate(f.path);
        }}
        onAskFree={() => {
          setOpen(null);
          setConfirm(f.path);
        }}
        onFree={() => {
          setConfirm(null);
          free.mutate(f.path);
        }}
        onCancelFree={() => setConfirm(null)}
        onReveal={() => {
          setOpen(null);
          reveal.mutate(f.path);
        }}
      />
    ));
  }

  const failure = warmup.error ?? free.error ?? reveal.error ?? null;
  if (path) {
    return (
      <div className="pb-1 pl-5" role="group" aria-label={it.disks.projects(path.split("/").pop() ?? "")}>
        {content}
        {failure && (
          <div className="px-1 pt-1">
            <ActionError error={failure} />
          </div>
        )}
      </div>
    );
  }
  return (
    <section className="mx-2 rounded-[14px] bg-sunken px-1 pt-3.5 pb-1.5">
      <div className="mb-1 flex items-baseline justify-between px-3 text-[11px] font-semibold tracking-[0.1em] text-ink-2 uppercase">
        <span>{it.folders.heading}</span>
        {folders.data && <b className="font-ro text-[12px] font-normal tracking-[0.02em] text-ink">{list.length}</b>}
      </div>
      {content}
      {failure && (
        <div className="px-1 pt-1">
          <ActionError error={failure} />
        </div>
      )}
    </section>
  );
}

/** The team's disks: one row each, its state on the LED, its projects inside. */
function DiskList({ status, team }: { status: DiskStatus; team: TeamView }) {
  const [open, setOpen] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  return (
    <section className="mx-2 rounded-[14px] bg-sunken px-1 pt-3.5 pb-1.5" aria-label={it.disks.heading}>
      <div className="mb-1 flex items-baseline justify-between px-3 text-[11px] font-semibold tracking-[0.1em] text-ink-2 uppercase">
        <span>{it.disks.heading}</span>
        {team.applying ? (
          <span className="text-[12px] font-normal tracking-normal normal-case">{it.team.applying}</span>
        ) : (
          <b className="font-ro text-[12px] font-normal tracking-[0.02em] text-ink">{team.disks.length}</b>
        )}
      </div>
      {team.disks.map((d) => (
        <DiskRow
          key={d.id}
          disk={d}
          status={status}
          applying={team.applying}
          menuOpen={open === d.id}
          expanded={expanded === d.id && !!d.path}
          onMenu={() => setOpen(open === d.id ? null : d.id)}
          onCloseMenu={() => setOpen(null)}
          onExpand={() => setExpanded(expanded === d.id ? null : d.id)}
        />
      ))}
    </section>
  );
}

interface DiskRowProps {
  disk: DiskView;
  status: DiskStatus;
  applying: boolean;
  menuOpen: boolean;
  expanded: boolean;
  onMenu: () => void;
  onCloseMenu: () => void;
  onExpand: () => void;
}

function DiskRow({ disk, status, applying, menuOpen, expanded, onMenu, onCloseMenu, onExpand }: DiskRowProps) {
  const look = diskLook(disk, applying);
  const live: LiveFile[] = useActivitySnapshot().data?.live ?? [];
  const arriving = disk.path ? diskArrivals(live, disk.id) : null;
  const [confirm, setConfirm] = useState<"off" | "free" | null>(null);
  const toggle = useDiskAction<boolean>((on) => backend.setDiskEnabled(disk.id, on));
  const free = useDiskAction<string>(backend.free);
  const reveal = useDiskAction<string | undefined>(backend.openInFinder);
  const busy = !!status.job;
  const working = !!disk.path && status.job?.path === disk.path;
  const failure = toggle.error ?? free.error ?? reveal.error ?? null;
  return (
    <>
      <div className={`flex min-h-12 items-center rounded-[10px] pr-1 pl-3 hover:bg-raised ${menuOpen || confirm ? "bg-raised" : ""}`}>
        <span className="mr-[9px] -ml-px flex">
          <Led state={working || arriving ? "dl" : (look.led ?? undefined)} blink={working || !!arriving || look.blink} />
        </span>
        <div className="min-w-0 flex-1 py-1.5">
          <div className="flex items-baseline gap-2">
            <button
              type="button"
              className="flex min-w-0 items-center gap-1 border-0 bg-transparent p-0 text-left text-ink disabled:cursor-default"
              disabled={!disk.path}
              aria-expanded={disk.path ? expanded : undefined}
              aria-label={disk.path ? it.disks.projects(disk.folder) : undefined}
              onClick={onExpand}
            >
              <span className={`truncate text-[13px] font-medium ${look.dim ? "text-ink-2" : ""}`}>{disk.folder}</span>
              {disk.path && (
                <ChevronRight
                  size={13}
                  strokeWidth={1.7}
                  aria-hidden="true"
                  className={`shrink-0 text-ink-3 transition-transform ${expanded ? "rotate-90" : ""}`}
                />
              )}
            </button>
            <Readout className="ml-auto shrink-0 text-[11px]">{working ? it.folders.working : look.readout}</Readout>
          </div>
          <span className="mt-px block text-[12px] leading-[1.35] text-ink-2">{arriving ? `${arriving} · ${look.line}` : look.line}</span>
        </div>
        <button
          type="button"
          className="grid size-[30px] place-items-center rounded-lg border-0 bg-transparent text-ink-2 hover:bg-sunken hover:text-ink"
          aria-label={it.disks.actions(disk.folder)}
          aria-expanded={menuOpen}
          onClick={() => {
            setConfirm(null);
            onMenu();
          }}
        >
          <Ellipsis size={16} strokeWidth={1.7} aria-hidden="true" />
        </button>
      </div>
      {menuOpen && (
        <div
          role="menu"
          className="mx-1 mt-1 mb-2 flex flex-col rounded-xl bg-raised p-1.5 shadow-[0_0_0_0.5px_rgba(0,0,0,0.12),0_12px_28px_-12px_rgba(40,30,20,0.35)]"
        >
          {disk.path && (
            <>
              <MenuItem
                onClick={() => {
                  onCloseMenu();
                  reveal.mutate(disk.path!);
                }}
                aside={<FolderIcon size={14} strokeWidth={1.7} aria-hidden="true" />}
              >
                {it.disks.showInFinder}
              </MenuItem>
              <MenuItem
                onClick={() => {
                  onCloseMenu();
                  void backend.openDisk(disk.id, "activity");
                }}
                aside={<History size={14} strokeWidth={1.7} aria-hidden="true" />}
              >
                {it.activity.open}
              </MenuItem>
              <MenuItem
                onClick={() => {
                  onCloseMenu();
                  void backend.openDisk(disk.id, "trash");
                }}
                aside={<Trash2 size={14} strokeWidth={1.7} aria-hidden="true" />}
              >
                {it.trash.open}
              </MenuItem>
              <MenuItem
                disabled={busy}
                onClick={() => {
                  onCloseMenu();
                  setConfirm("free");
                }}
              >
                {it.disks.freeSpace}
              </MenuItem>
            </>
          )}
          {/* A disk the team took away is not the person's to turn on or off. */}
          {disk.listed && (
            <MenuItem
              disabled={toggle.isPending}
              onClick={() => {
                onCloseMenu();
                if (disk.enabled) setConfirm("off");
                else toggle.mutate(true);
              }}
            >
              {disk.enabled ? it.disks.turnOff : it.disks.turnOn}
            </MenuItem>
          )}
        </div>
      )}
      {confirm === "off" && (
        <Confirm
          className="mx-1 mt-1 mb-2"
          title={it.disks.offConfirm.title(disk.folder)}
          body={it.disks.offConfirm.body}
          confirm={it.disks.offConfirm.confirm}
          cancel={it.panel.cancel}
          onConfirm={() => {
            setConfirm(null);
            toggle.mutate(false);
          }}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === "free" && disk.path && (
        <Confirm
          className="mx-1 mt-1 mb-2"
          title={it.folders.freeConfirm.title(disk.folder)}
          body={it.folders.freeConfirm.body}
          confirm={it.folders.freeConfirm.confirm}
          cancel={it.panel.cancel}
          onConfirm={() => {
            setConfirm(null);
            free.mutate(disk.path!);
          }}
          onCancel={() => setConfirm(null)}
        />
      )}
      {failure && (
        <div className="px-1 pt-1">
          <ActionError error={failure} />
        </div>
      )}
      {expanded && disk.path && <FolderList status={status} path={disk.path} disk={disk.id} />}
    </>
  );
}

interface FolderRowProps {
  folder: Folder;
  /** A colleague copying into it or writing in it. */
  live: { kind: "arriving" | "in_use"; line: string } | null;
  working: boolean;
  open: boolean;
  confirming: boolean;
  busy: boolean;
  nearlyFull: boolean;
  onToggle: () => void;
  onWarmup: () => void;
  onAskFree: () => void;
  onFree: () => void;
  onCancelFree: () => void;
  onReveal: () => void;
}

function FolderRow(p: FolderRowProps) {
  const active = p.open || p.confirming;
  return (
    <>
      <div className={`flex min-h-9 items-center rounded-[10px] pr-1 pl-3 hover:bg-raised ${active ? "bg-raised" : ""}`}>
        {p.working || p.live ? (
          <span className="mr-[9px] -ml-px flex">
            <Led state="dl" blink={p.working || p.live?.kind === "arriving"} />
          </span>
        ) : (
          <SlotMark />
        )}
        <span className="min-w-0 flex-1 py-1">
          <span className="block truncate text-[13px]">{p.folder.name}</span>
          {p.live && !p.working && <span className="block truncate text-[12px] leading-[1.35] text-ink-2">{p.live.line}</span>}
        </span>
        {p.working ? (
          <Readout className="mr-1 text-[11px]">{it.folders.working}</Readout>
        ) : (
          p.live && <Readout className="mr-1 ml-2 shrink-0 text-[11px]">{p.live.kind === "arriving" ? it.live.arriving : it.live.inUse}</Readout>
        )}
        <button
          type="button"
          className="grid size-[30px] place-items-center rounded-lg border-0 bg-transparent text-ink-2 hover:bg-sunken hover:text-ink"
          aria-label={it.folders.actions(p.folder.name)}
          aria-expanded={p.open}
          onClick={p.onToggle}
        >
          <Ellipsis size={16} strokeWidth={1.7} aria-hidden="true" />
        </button>
      </div>
      {p.open && (
        <div
          role="menu"
          className="mx-1 mt-1 mb-2 flex flex-col rounded-xl bg-raised p-1.5 shadow-[0_0_0_0.5px_rgba(0,0,0,0.12),0_12px_28px_-12px_rgba(40,30,20,0.35)]"
        >
          <MenuItem disabled={p.busy} onClick={p.onWarmup}>
            {it.folders.makeAvailable}
          </MenuItem>
          {p.nearlyFull && <p className="m-0 px-2.5 pt-1 pb-2.5 text-[13px] leading-[1.45] text-ink-2">{it.folders.fullCache}</p>}
          <MenuItem disabled={p.busy} onClick={p.onAskFree}>
            {it.folders.freeSpace}
          </MenuItem>
          <MenuItem onClick={p.onReveal} aside={<FolderIcon size={14} strokeWidth={1.7} aria-hidden="true" />}>
            {it.folders.showInFinder}
          </MenuItem>
        </div>
      )}
      {p.confirming && (
        <Confirm
          className="mx-1 mt-1 mb-2"
          title={it.folders.freeConfirm.title(p.folder.name)}
          body={it.folders.freeConfirm.body}
          confirm={it.folders.freeConfirm.confirm}
          cancel={it.panel.cancel}
          onConfirm={p.onFree}
          onCancel={p.onCancelFree}
        />
      )}
    </>
  );
}

function MenuItem({ children, aside, disabled, onClick }: { children: ReactNode; aside?: ReactNode; disabled?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className="flex h-9 items-center justify-between gap-2.5 rounded-lg border-0 bg-transparent px-2.5 text-left text-[13px] font-medium text-ink hover:bg-sunken disabled:opacity-50"
    >
      {children}
      {aside && <span className="font-ro text-[11.5px] text-ink-2">{aside}</span>}
    </button>
  );
}

function Footer({ status, version }: { status: DiskStatus; version: string | undefined }) {
  const [confirm, setConfirm] = useState(false);
  const stop = useDiskAction(backend.stop);
  const canStop = status.state === "connected" || status.state === "unreachable" || status.state === "no_disks";
  return (
    <>
      {confirm && (
        <Confirm
          className="mx-2 mt-2"
          title={it.panel.stopConfirm.title}
          body={it.panel.stopConfirm.body}
          confirm={it.panel.stopConfirm.confirm}
          cancel={it.panel.cancel}
          onConfirm={() => {
            setConfirm(false);
            stop.mutate();
          }}
          onCancel={() => setConfirm(false)}
        />
      )}
      {stop.error && (
        <div className="mx-2 mt-2">
          <ActionError error={stop.error} />
        </div>
      )}
      <footer className="flex items-center gap-1.5 py-2.5 pr-2.5 pl-5 text-[12px] text-ink-3">
        <span className="mr-auto font-ro text-[12px]">{version ? it.version(version) : it.product}</span>
        {/* The team's disks open their activity and trash from their own menu. */}
        {status.mode === "single" && status.state === "connected" && (
          <>
            <button
              type="button"
              className="grid size-[30px] place-items-center rounded-lg border-0 bg-transparent text-ink-2 hover:bg-sunken hover:text-ink"
              aria-label={it.activity.open}
              title={it.activity.open}
              onClick={() => void backend.openDisk(null, "activity")}
            >
              <History size={16} strokeWidth={1.7} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="grid size-[30px] place-items-center rounded-lg border-0 bg-transparent text-ink-2 hover:bg-sunken hover:text-ink"
              aria-label={it.trash.open}
              title={it.trash.open}
              onClick={() => void backend.openDisk(null, "trash")}
            >
              <Trash2 size={16} strokeWidth={1.7} aria-hidden="true" />
            </button>
          </>
        )}
        <button
          type="button"
          className="grid size-[30px] place-items-center rounded-lg border-0 bg-transparent text-ink-2 hover:bg-sunken hover:text-ink"
          aria-label={it.panel.settings}
          title={it.panel.settings}
          onClick={() => void backend.openSettings()}
        >
          <Settings size={16} strokeWidth={1.7} aria-hidden="true" />
        </button>
        {canStop && (
          <Button variant="ghost" size="sm" disabled={!!status.job} onClick={() => setConfirm(true)}>
            <Power size={14} strokeWidth={1.7} aria-hidden="true" />
            {it.panel.turnOff}
          </Button>
        )}
      </footer>
    </>
  );
}
