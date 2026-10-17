import { describe, expect, it as test } from "vitest";
import { lostAccess } from "@/features/account/view";
import { diskLook, noDisksCase, teamOf } from "@/features/disk/team";
import { it } from "@/i18n/it";
import type { AuthView, DiskStatus, DiskView, TeamView } from "@/lib/types";

const mounted: DiskView = {
  id: "0192-a",
  folder: "Archivio 2025",
  path: "/Volumes/Floppy/Archivio 2025",
  access: "write",
  enabled: true,
  state: "mounted",
  mounted_access: "write",
  pending: null,
  cache_used_mb: 812,
  cache_size_mb: 10240,
  replica: "up",
  meta_mode: "replica",
  online: true,
};
const r = it.disks.readout;
const look = (over: Partial<DiskView>, applying = false) => diskLook({ ...mounted, ...over }, applying);

describe("diskLook", () => {
  test("a mounted disk is green, with its local copies", () => {
    expect(look({})).toMatchObject({ led: "ok", blink: false, readout: r.mounted, dim: false });
    expect(look({}).line).toBe("0,8 GB su questo Mac");
    expect(look({ cache_used_mb: 0 }).line).toBe(it.disks.line.mounted);
  });

  test("read-only is green and says so", () => {
    const l = look({ access: "read", mounted_access: "read" });
    expect(l).toMatchObject({ led: "ok", readout: r.readOnly });
    expect(l.line).toContain(it.disks.line.readOnly);
  });

  test("offline is red: reads go on, saving does not", () => {
    expect(look({ online: false, meta_mode: "offline" })).toMatchObject({ led: "err", readout: r.offline });
    expect(look({ online: false, meta_mode: "offline" }).line).toContain(it.disks.line.offline);
    const readOnly = look({ access: "read", mounted_access: "read", online: false, meta_mode: "offline" });
    expect(readOnly.line).toContain(it.disks.line.offlineRead);
  });

  test("a remount that waits says which way it goes", () => {
    const lowering = look({ access: "read", mounted_access: "write", pending: "remount" });
    expect(lowering).toMatchObject({ led: "dl", blink: false, readout: r.waiting });
    expect(lowering.line).toContain(it.disks.line.becomingReadOnly);
    expect(look({ access: "write", mounted_access: "read", pending: "remount" }).line).toContain(
      it.disks.line.becomingWritable,
    );
    expect(look({ pending: "remount" }).line).toContain(it.disks.line.updating);
  });

  test("starting blinks; failed and stuck are red", () => {
    expect(look({ state: "starting" })).toMatchObject({ led: "dl", blink: true, readout: r.starting });
    expect(look({ state: null }, true)).toMatchObject({ led: "dl", blink: true });
    expect(look({ state: "failed" })).toMatchObject({ led: "err", readout: r.failed });
    expect(look({ state: "stopped" })).toMatchObject({ led: "err", line: it.disks.line.stopped });
    expect(look({ state: "stopped" }, true)).toMatchObject({ led: "dl", blink: true });
  });

  test("turned off is unlit and quiet, unless still in use", () => {
    expect(look({ enabled: false, state: "disabled" })).toMatchObject({ led: null, readout: r.off, dim: true });
    expect(look({ enabled: false, pending: "remove" })).toMatchObject({ led: "dl", readout: r.leaving, dim: false });
  });
});

const team: TeamView = {
  disks: [mounted],
  rejected: [],
  applying: false,
  error: null,
  restart_needed: false,
  refused_single: false,
};
const base = { server: "s", server_display: "s", default_server: "s", busy: null, error: null, reachable: true } as const;
const signedIn: AuthView = {
  ...base,
  phase: "signed_in",
  email: "g@x.it",
  device_name: "Mac",
  organization: "Studio",
  user_name: "Giulia",
};

describe("noDisksCase", () => {
  test("names why a ready Floppy shows no disk", () => {
    expect(noDisksCase(null, { ...base, phase: "signed_out", notice: null })).toBe("signed_out");
    expect(noDisksCase(null, undefined)).toBe("signed_out");
    expect(noDisksCase(null, { ...base, phase: "registering", email: "g@x.it", device_name: "Mac" })).toBe("registering");
    expect(noDisksCase({ ...team, disks: [] }, signedIn)).toBe("none");
    expect(noDisksCase({ ...team, disks: [{ ...mounted, enabled: false }] }, signedIn)).toBe("all_off");
  });
});

describe("teamOf", () => {
  test("the team view only where team disks are shown", () => {
    const status = { mode: "team", team } as DiskStatus;
    expect(teamOf(status)).toBe(team);
    expect(teamOf({ mode: "single", team: null } as DiskStatus)).toBeNull();
    const refused = { ...team, refused_single: true };
    expect(teamOf({ mode: "single", team: refused } as DiskStatus)).toBe(refused);
  });
});

describe("lostAccess", () => {
  test("each reason has its own words; the person's own sign-out has none", () => {
    expect(lostAccess("revoked")?.title).toBe(it.account.revoked.title);
    expect(lostAccess("unknown")?.title).toBe(it.account.unknown.title);
    expect(lostAccess("already_signed_out")?.title).toBe(it.account.alreadySignedOut.title);
    expect(lostAccess("signed_out")).toBeNull();
    expect(lostAccess(null)).toBeNull();
  });
});
