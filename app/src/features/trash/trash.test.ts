import { describe, expect, it as test } from "vitest";
import { byDay, dayLabel, daysLeft, fromLine, matches, restoredLine, restoreLabel } from "@/features/trash/trash";
import { bytes } from "@/lib/format";
import type { TrashEntry } from "@/lib/types";

const DAY = 86400;
// 19 October 2026, 15:00 local time.
const now = new Date(2026, 9, 19, 15, 0, 0).getTime() / 1000;

function entry(over: Partial<TrashEntry> = {}): TrashEntry {
  return {
    id: "2026-10-19-07/1-2",
    name: "clip.mov",
    kind: "file",
    from: "Cantine/Girato",
    deleted_at: now - 3600,
    expires_at: now - 3600 + 7 * DAY,
    size: 1024,
    replaced: false,
    by_this_mac: false,
    ...over,
  };
}

describe("trash", () => {
  test("names the day of a delete", () => {
    expect(dayLabel(now - 3600, now)).toBe("Oggi");
    expect(dayLabel(now - DAY, now)).toBe("Ieri");
    expect(dayLabel(new Date(2026, 9, 12, 10).getTime() / 1000, now)).toBe("Lunedì 12 ottobre");
  });

  test("counts whole days left", () => {
    expect(daysLeft(now + 6.5 * DAY, now)).toBe(6);
    expect(daysLeft(now + 0.5 * DAY, now)).toBe(0);
    expect(daysLeft(now - DAY, now)).toBe(0);
  });

  test("groups by day, newest first", () => {
    const groups = byDay([entry({ id: "a", deleted_at: now - 2 * DAY }), entry({ id: "b" }), entry({ id: "c", deleted_at: now - 60 })], now);
    expect(groups.map((g) => [g.label, g.items.map((e) => e.id)])).toEqual([
      ["Oggi", ["c", "b"]],
      ["Sabato 17 ottobre", ["a"]],
    ]);
  });

  test("filters by kind and by name or folder", () => {
    const old = entry({ replaced: true, name: "Musica.xlsx", from: "Pastificio" });
    expect(matches(old, "", "deleted")).toBe(false);
    expect(matches(old, "pastif", "replaced")).toBe(true);
    expect(matches(entry(), "GIRATO", "all")).toBe(true);
    expect(matches(entry(), "export", "all")).toBe(false);
  });

  test("says where an entry was and how it comes back", () => {
    expect(fromLine(entry(), "Progetti")).toBe("Da Cantine/Girato");
    expect(fromLine(entry({ from: "" }), "Progetti")).toBe("Da Progetti");
    expect(fromLine(entry({ from: null }), "Progetti")).toBe("La sua cartella non c'è più: torna in cima al disco");
    expect(fromLine(entry({ kind: "folder", files: 120 }), "Progetti")).toBe("Cartella con 120 file · da Cantine/Girato");
    expect(fromLine(entry({ replaced: true }), "Progetti")).toBe("Versione precedente · era in Cantine/Girato");
    expect(restoreLabel(entry({ replaced: true }))).toBe("Ripristina come copia");
    const back = { id: "h/1-2", disk: null, path: "a", name: "a (ripristinato).mov", renamed: true, moved: false, finder_path: null };
    expect(restoredLine(back)).toBe("Ripristinato come «a (ripristinato).mov»");
    expect(restoredLine({ ...back, moved: true })).toBe("Ripristinato in cima al disco");
  });

  test("writes sizes in bytes", () => {
    expect(bytes(84 * 1024)).toBe("84 KB");
    expect(bytes(412 * 1024 * 1024)).toBe("412 MB");
    expect(bytes(3.9 * 1024 ** 3)).toBe("3,9 GB");
    expect(bytes(3)).toBe("1 KB");
    expect(bytes(0)).toBe("0 KB");
  });
});
