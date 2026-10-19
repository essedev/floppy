import { describe, expect, it as test } from "vitest";
import { diskArrivals, group, matches, meta, people, projectLive, restoreItems, sentence } from "@/features/activity/activity";
import type { FeedEvent, LiveFile } from "@/lib/types";

const MARCO = { name: "Marco", you: false };
const YOU = { name: "", you: true };
let n = 0;
function ev(over: Partial<FeedEvent>): FeedEvent {
  n += 1;
  return { id: `e${n}`, disk: "d", at: 1000, op: "added", kind: "file", path: "Cantine/Girato/a.mov", to: null, size: 1024, by: MARCO, this_mac: false, ...over };
}

describe("group", () => {
  test("close copies of one person in one folder are one row", () => {
    const rows = group([
      ev({ at: 1000, path: "Cantine/Girato/a.mov" }),
      ev({ at: 1050, path: "Cantine/Girato/b.mov" }),
      ev({ at: 1100, path: "Cantine/Girato/c.mov" }),
      // Too late, another folder, another person: rows of their own.
      ev({ at: 1400, path: "Cantine/Girato/d.mov" }),
      ev({ at: 1110, path: "Cantine/Audio/e.wav" }),
      ev({ at: 1120, path: "Cantine/Girato/f.mov", by: YOU, this_mac: true }),
    ]);
    expect(rows.map((r) => r.files)).toEqual([1, 1, 1, 3]);
    const three = rows[3]!;
    expect(sentence(three, "Progetti")).toEqual({ who: "Marco", what: "ha aggiunto 3 file in Cantine/Girato" });
    expect(meta(three)).toBe("3 file · 3 KB");
    expect(sentence(rows[1]!, "Progetti")).toEqual({ who: "Tu", what: "hai aggiunto f.mov in Cantine/Girato" });
  });

  test("a deleted folder takes its files, wherever they were inside", () => {
    const rows = group([
      ev({ op: "deleted", path: "Pastificio/Girato grezzo/1.mov", at: 2000, size: 4096 }),
      ev({ op: "deleted", path: "Pastificio/Girato grezzo/Raw/2.mov", at: 2001, size: 2048 }),
      ev({ op: "deleted", kind: "folder", path: "Pastificio/Girato grezzo/Raw", at: 2002, size: null }),
      ev({ op: "deleted", kind: "folder", path: "Pastificio/Girato grezzo", at: 2003, size: null }),
      ev({ op: "deleted", path: "Pastificio/altro.mov", at: 2004 }),
    ]);
    expect(rows).toHaveLength(2);
    const folder = rows.find((r) => r.kind === "folder")!;
    expect(sentence(folder, "Progetti").what).toBe("ha cancellato la cartella Girato grezzo da Pastificio");
    expect(meta(folder)).toBe("2 file · 6 KB");
    expect(restoreItems(folder)).toEqual([{ path: "Pastificio/Girato grezzo", at: 2003 }]);
  });

  test("renames and moves, at the top of the disk too", () => {
    const [moved, renamed] = group([
      ev({ op: "renamed", kind: "folder", path: "Fondazione Lirica - Backstage", to: "Fondazione Lirica - Backstage Tosca", at: 10 }),
      ev({ op: "renamed", path: "Logo_cliente.png", to: "Showreel 2026/Grafiche/Logo_cliente.png", at: 20, by: YOU }),
    ]);
    expect(sentence(moved!, "Progetti")).toEqual({ who: "Tu", what: "hai spostato Logo_cliente.png in Showreel 2026/Grafiche" });
    expect(sentence(renamed!, "Progetti").what).toBe("ha rinominato Fondazione Lirica - Backstage in Fondazione Lirica - Backstage Tosca");
  });

  test("filters by person and type", () => {
    const rows = group([ev({ op: "replaced", at: 1 }), ev({ op: "added", at: 500, by: YOU })]);
    expect(rows.filter((r) => matches(r, "all", "del"))).toHaveLength(1);
    expect(meta(rows.find((r) => r.op === "replaced")!)).toBe("La versione precedente è nel cestino");
    expect(rows.filter((r) => matches(r, "Marco", "add"))).toHaveLength(0);
    expect(people(rows).map((p) => p.label)).toEqual(["Tu", "Marco"]);
  });
});

describe("live", () => {
  const live = (over: Partial<LiveFile>): LiveFile => ({
    disk: "d",
    path: "Cantine Arnaldi - Vendemmia/Girato/A014.mov",
    op: "added",
    since: 0,
    size: 512 * 1024 * 1024,
    speed: 8 * 1024 * 1024,
    by: MARCO,
    this_mac: false,
    ...over,
  });

  test("arriving and in use on a project's row", () => {
    expect(projectLive([live({})], "d", "Cantine Arnaldi - Vendemmia")).toEqual({
      kind: "arriving",
      line: "Da Marco: 512 MB finora, circa 8 MB/s",
    });
    const writing = live({ op: "written", path: "Fondazione/Export/Backstage_finale.mp4", by: { name: "Simone", you: false } });
    expect(projectLive([writing], "d", "Fondazione")).toEqual({ kind: "in_use", line: "Simone sta scrivendo Backstage_finale.mp4" });
    // This Mac's own copies are not news on its own panel.
    expect(projectLive([live({ this_mac: true, by: YOU })], "d", "Cantine Arnaldi - Vendemmia")).toBeNull();
    expect(diskArrivals([live({}), live({ path: "x/y.mov" })], "d")).toBe("2 file in arrivo da Marco");
  });
});
