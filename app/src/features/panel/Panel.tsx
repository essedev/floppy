/**
 * The panel under the menu bar icon (concept-c-app, DECISIONS #12): the
 * faceplate first, then the state in one sentence with one large action, the
 * space on this Mac, the projects with their row menu, and the footer.
 */

import { type ReactNode, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Ellipsis, ExternalLink, Folder as FolderIcon, Power, RefreshCw, Settings } from "lucide-react";
import { Faceplate } from "@/components/Faceplate";
import { Button, Confirm, Led, Notice, Readout, SlotMark } from "@/components/ui";
import { useAuth } from "@/features/account/hooks";
import { faceFor, isBusy } from "@/features/disk/face";
import { keys, useAppInfo, useCountdown, useDiskAction, useFolders, useStatus } from "@/features/disk/hooks";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import { gb, gbUnit } from "@/lib/format";
import type { CommandError, DiskStatus, Folder } from "@/lib/types";

const WITH_SPACE = new Set(["connected", "unreachable", "starting", "stopping"]);

export function Panel() {
  const { data: status } = useStatus();
  const info = useAppInfo();
  const qc = useQueryClient();
  const root = useRef<HTMLDivElement>(null);

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

  return (
    <div ref={root} className="panel flex w-[352px] flex-col overflow-hidden rounded-[20px] bg-surface" aria-label={it.panel.label}>
      {status ? (
        <>
          <div className="mx-2 mt-2">
            <Faceplate {...faceFor(status)} />
          </div>
          <StatusSection status={status} />
          <RevokedSection />
          {WITH_SPACE.has(status.state) && status.space?.cache_used_mb != null && <SpaceSection status={status} />}
          {status.state === "connected" && <FolderList status={status} />}
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
  if (auth?.phase !== "signed_out" || auth.notice !== "revoked") return null;
  return (
    <div className="px-4 pb-3">
      <Notice led="err" title={it.account.revoked.title}>
        {it.account.revoked.body}
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
  const start = useDiskAction(backend.start);
  const install = useDiskAction(backend.installEngine);
  const cancel = useDiskAction(backend.cancel);
  const openFinder = useDiskAction<string | undefined>(backend.openInFinder);
  const freeAll = useDiskAction<string>(backend.free);
  const [confirmFree, setConfirmFree] = useState(false);

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
        title = it.panel.connected.title;
        body = it.panel.connected.body;
        actions = finder;
      }
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

  const failure = start.error ?? install.error ?? cancel.error ?? openFinder.error ?? freeAll.error ?? null;
  return (
    <section className="flex flex-col gap-4 px-5 pt-5 pb-[22px]">
      <div>
        <h1 className="m-0 text-[20px] leading-[1.2] font-bold tracking-[-0.02em]">{title}</h1>
        {body && <p className="mt-1 mb-0 text-[13.5px] text-ink-2">{body}</p>}
      </div>
      {extra}
      <div className="flex gap-2">{actions}</div>
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

function FolderList({ status }: { status: DiskStatus }) {
  const folders = useFolders(status.state === "connected" && !!status.mount_point);
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
    content = <p className="m-0 px-3 py-2 text-[13px] text-ink-2">{it.folders.empty}</p>;
  } else {
    content = list.map((f) => (
      <FolderRow
        key={f.path}
        folder={f}
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

interface FolderRowProps {
  folder: Folder;
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
        {p.working ? (
          <span className="mr-[9px] -ml-px flex">
            <Led state="dl" blink />
          </span>
        ) : (
          <SlotMark />
        )}
        <span className="min-w-0 flex-1 truncate text-[13px]">{p.folder.name}</span>
        {p.working && <Readout className="mr-1 text-[11px]">{it.folders.working}</Readout>}
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
  const canStop = status.state === "connected" || status.state === "unreachable";
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
