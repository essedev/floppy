/**
 * The disk window (concept-f-trash-activity 2A2, DECISIONS #27): the disks
 * this Mac has mounted on the left, and for the chosen one two tabs, its
 * activity and its trash. The panel's disk menu opens it on either tab.
 */

import { useEffect, useState } from "react";
import { ActivityTab } from "@/features/activity/ActivityTab";
import { useStatus } from "@/features/disk/hooks";
import { TrashTab } from "@/features/trash/TrashTab";
import { it } from "@/i18n/it";
import { backend } from "@/lib/backend";
import type { DiskStatus, DiskWindowTarget } from "@/lib/types";

/** A disk as the window lists it; `disk` is null for the single-user volume. */
export interface WindowDisk {
  key: string;
  disk: string | null;
  name: string;
  writable: boolean;
}

/** The disks mounted now: the team's, or the single-user volume. */
export function windowDisks(status: DiskStatus | undefined): WindowDisk[] {
  if (!status) return [];
  if (status.team && !status.team.refused_single) {
    return status.team.disks
      .filter((d) => d.path)
      .map((d) => ({ key: d.id, disk: d.id, name: d.folder, writable: (d.mounted_access ?? d.access) === "write" }));
  }
  if (status.mount_point) return [{ key: "-", disk: null, name: it.trash.thisDisk, writable: true }];
  return [];
}

export function DiskWindow() {
  const { data: status } = useStatus();
  const [tab, setTab] = useState<DiskWindowTarget["tab"]>("activity");
  const [chosen, setChosen] = useState<string | null>(null);
  const [search, setSearch] = useState<string | undefined>(undefined);
  useEffect(
    () =>
      backend.onDiskWindow((t) => {
        setChosen(t.disk ?? "-");
        setTab(t.tab);
        setSearch(undefined);
      }),
    [],
  );
  const disks = windowDisks(status);
  const current = disks.find((d) => d.key === chosen) ?? disks[0];

  return (
    <main className="flex h-screen bg-surface text-ink">
      <aside className="flex w-[210px] shrink-0 flex-col gap-1 border-r border-line px-2.5 pt-4 pb-3">
        <h2 className="m-0 mb-1 ml-2 text-[11px] font-semibold tracking-[0.1em] text-ink-3 uppercase">{it.trash.disks}</h2>
        {disks.map((d) => (
          <button
            key={d.key}
            type="button"
            aria-pressed={d === current}
            onClick={() => {
              setChosen(d.key);
              setSearch(undefined);
            }}
            className={`flex items-center gap-2 rounded-lg border-0 px-2.5 py-2 text-left text-[13px] ${
              d === current ? "bg-raised font-semibold" : "bg-transparent hover:bg-sunken"
            }`}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate">{d.name}</span>
              {!d.writable && <small className="block text-[11.5px] font-normal text-ink-2">{it.trash.readOnly}</small>}
            </span>
          </button>
        ))}
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-3 border-b border-line px-6 py-3">
          <b className="min-w-0 truncate text-[15px]">{current?.name ?? ""}</b>
          <div role="tablist" aria-label={it.disk.tabs} className="ml-auto flex rounded-[10px] bg-sunken p-0.5">
            {(["activity", "trash"] as const).map((k) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={tab === k}
                onClick={() => {
                  setTab(k);
                  setSearch(undefined);
                }}
                className={`h-8 rounded-lg border-0 px-3.5 text-[13px] ${tab === k ? "bg-raised font-semibold text-ink" : "bg-transparent text-ink-2"}`}
              >
                {it.disk[k]}
              </button>
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {!current ? (
            <div className="flex h-full flex-col items-center justify-center gap-1.5 px-10 text-center">
              <b className="text-[15px]">{it.trash.noDisks.title}</b>
              <p className="m-0 max-w-[420px] text-[13px] leading-[1.45] text-ink-2">{it.trash.noDisks.body}</p>
            </div>
          ) : tab === "trash" ? (
            <TrashTab disk={current.disk} search={search} />
          ) : (
            <ActivityTab
              key={current.key}
              disk={current}
              onTrash={(name) => {
                setSearch(name);
                setTab("trash");
              }}
            />
          )}
        </div>
      </section>
    </main>
  );
}
