import { describe, expect, it as test } from "vitest";
import { DONE_SHOWN_S, etaText, uploadDiskLine, uploadRows, uploadSummary, uploadTitle } from "@/features/activity/uploads";
import type { Sending, UploadDisk, UploadFile, UploadsView } from "@/lib/types";

const MB = 1024 * 1024;
const NOW = 1_800_000_000;
const A = "progetti";
const B = "archivio";

function sending(path: string, over: Partial<Sending> = {}): Sending {
  return { disk: A, path, size: 900 * MB, since: NOW - 60, copying: false, ...over };
}
function disk(over: Partial<UploadDisk> = {}): UploadDisk {
  return { disk: A, waiting: 1500 * MB, rate: 5 * MB, paused: false, ...over };
}
function file(path: string, state: UploadFile["state"], over: Partial<UploadFile> = {}): UploadFile {
  return { disk: A, path, size: 2100 * MB, closed: NOW - 30, state, reason: state === "incomplete" ? "finder" : null, finder_path: `/Volumes/Floppy/Progetti/${path}`, ...over };
}
const view = (over: Partial<UploadsView>): UploadsView => ({ sending: [], disks: [], files: [], ...over });

describe("uploadSummary", () => {
  test("nothing to say without uploads", () => {
    expect(uploadSummary(undefined, undefined, NOW).phase).toBeNull();
    expect(uploadTitle(uploadSummary(view({}), undefined, NOW))).toBeNull();
  });

  test("the most urgent state comes first: incomplete, paused, sending, check, done", () => {
    const all = view({
      sending: [sending("Spot/C0315.MP4")],
      disks: [disk({ paused: true })],
      files: [file("Spot/C0272.MP4", "incomplete"), file("Spot/x.zip", "check"), file("Spot/ok.mp4", "done")],
    });
    expect(uploadSummary(all, undefined, NOW).phase).toBe("incomplete");
    expect(uploadSummary({ ...all, files: all.files.slice(1) }, undefined, NOW).phase).toBe("paused");
    expect(uploadSummary({ ...all, disks: [disk()], files: all.files.slice(1) }, undefined, NOW).phase).toBe("sending");
    expect(uploadSummary(view({ files: all.files.slice(1) }), undefined, NOW).phase).toBe("check");
    expect(uploadSummary(view({ files: all.files.slice(2) }), undefined, NOW).phase).toBe("done");
  });

  test("a finished upload says so for a few minutes, then nothing", () => {
    const v = view({ files: [file("Spot/ok.mp4", "done", { closed: NOW - DONE_SHOWN_S - 1 })] });
    expect(uploadSummary(v, undefined, NOW).phase).toBeNull();
    // Being checked still counts as arrived: its data is in the bucket.
    expect(uploadSummary(view({ files: [file("Spot/ok.mp4", "checking")] }), undefined, NOW).phase).toBe("done");
  });

  test("one disk sees only its own", () => {
    const v = view({ disks: [disk(), disk({ disk: B, paused: true })], sending: [sending("Spot/a.MP4")] });
    expect(uploadSummary(v, A, NOW).phase).toBe("sending");
    expect(uploadSummary(v, B, NOW).phase).toBe("paused");
    expect(uploadSummary(v, undefined, NOW).waiting).toBe(3000 * MB);
  });
});

describe("the panel's words", () => {
  test("the time left, once the speed is known", () => {
    expect(etaText(1500 * MB, 0)).toBeNull();
    expect(etaText(100 * MB, 5 * MB)).toBe("meno di un minuto");
    expect(etaText(1500 * MB, 5 * MB)).toBe("circa 5 minuti");
    expect(etaText(41_000 * MB, 5 * MB)).toBe("circa 2 ore e 20 minuti");
  });

  test("the title says whether the Mac can be closed", () => {
    const up = uploadTitle(uploadSummary(view({ sending: [sending("Spot/a.MP4"), sending("Spot/b.MP4")], disks: [disk()] }), undefined, NOW))!;
    expect(up.title).toBe("Sto caricando 2 file");
    expect(up.body).toBe("Mancano 1,5 GB, circa 5 minuti. Lascia il Mac acceso: lo tengo sveglio io.");
    const paused = uploadTitle(uploadSummary(view({ disks: [disk({ paused: true })] }), undefined, NOW))!;
    expect(paused.title).toBe("Caricamento in pausa");
    const bad = uploadTitle(uploadSummary(view({ files: [file("Spot/C0272.MP4", "incomplete")] }), undefined, NOW))!;
    expect(bad).toEqual({ title: "1 copia non completata", body: "C0272.MP4 è arrivato a metà: lo trovi qui sotto, da ricopiare." });
    const done = uploadTitle(uploadSummary(view({ files: [file("Spot/a.mp4", "done"), file("Spot/b.mp4", "done")] }), undefined, NOW))!;
    expect(done).toEqual({ title: "Tutto caricato", body: "2 file sono su Floppy. Puoi chiudere il Mac." });
  });

  test("names travel in the Finder's form", () => {
    const s = uploadSummary(view({ files: [file("Spot/A  B.MP4", "incomplete")] }), undefined, NOW);
    expect(uploadTitle(s)!.body).toContain("A / B.MP4");
    expect(uploadRows(s).pinned[0]!.name).toBe("A / B.MP4");
  });

  test("a disk's line", () => {
    expect(uploadDiskLine(uploadSummary(view({ sending: [sending("a.MP4")], disks: [disk()] }), A, NOW))).toEqual({
      line: "1 file in invio · circa 5 minuti",
      led: "dl",
      blink: true,
    });
    expect(uploadDiskLine(uploadSummary(view({ files: [file("a.MP4", "incomplete")] }), A, NOW))!.led).toBe("err");
    expect(uploadDiskLine(uploadSummary(view({ files: [file("a.MP4", "done")] }), A, NOW))).toBeNull();
  });
});

describe("uploadRows", () => {
  test("problems are pinned, the rest waits behind the list", () => {
    const s = uploadSummary(
      view({
        sending: [sending("Spot/C0315.MP4", { copying: true, size: 420 * MB })],
        disks: [disk()],
        files: [file("Spot/C0272.MP4", "incomplete"), file("Spot/Pillola.mp4", "done", { size: 139 * MB })],
      }),
      A,
      NOW,
    );
    const { pinned, rest } = uploadRows(s);
    expect(pinned.map((r) => [r.name, r.line, r.readout, r.led])).toEqual([["C0272.MP4", "Arrivati 2,1 GB · da ricopiare", "Incompleto", "err"]]);
    expect(rest.map((r) => [r.name, r.line, r.readout])).toEqual([
      ["C0315.MP4", "420 MB copiati", "Copia"],
      ["Pillola.mp4", "139 MB · su Floppy", "Caricato"],
    ]);
    expect(pinned[0]!.file?.finder_path).toBe("/Volumes/Floppy/Progetti/Spot/C0272.MP4");
  });

  test("a video cut short says so; a paused file waits", () => {
    const s = uploadSummary(
      view({ sending: [sending("Spot/a.MP4")], disks: [disk({ paused: true })], files: [file("Spot/b.MP4", "incomplete", { reason: "video" })] }),
      A,
      NOW,
    );
    const { pinned, rest } = uploadRows(s);
    expect(pinned[0]!.line).toBe("Il video è troncato · da ricopiare");
    expect([rest[0]!.line, rest[0]!.readout, rest[0]!.blink]).toEqual(["Copiato, in attesa della connessione", "In pausa", false]);
  });
});
