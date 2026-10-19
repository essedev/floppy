#!/usr/bin/env python3
"""
Tests of macos/vm/guest/floppy-activity: the activity on a real audit log, and the trash
on a folder laid out like JuiceFS's. The audit log, fixtures/audit-samba-4.22.log, is
what Samba 4.22 wrote while a Mac made a folder, copied a clip with the Finder, saved a
file by rename and in place, replaced the clip from the Finder, renamed it, made and deleted
a hidden file, renamed the folder and deleted a file and a folder (paths moved under
/mnt/drive/Progetti).

  python3 macos/tests/activity_test.py
"""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from calendar import timegm
from pathlib import Path

# No bytecode next to the guest files: build-engine.sh copies that folder into the app.
sys.dont_write_bytecode = True
here = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader(
    "floppy_activity", str(here / "../vm/guest/floppy-activity")
)
spec = importlib.util.spec_from_loader("floppy_activity", loader)
fa = importlib.util.module_from_spec(spec)
sys.modules["floppy_activity"] = fa  # dataclasses look their module up
loader.exec_module(fa)

FIXTURE = (here / "fixtures/audit-samba-4.22.log").read_text().splitlines(keepends=True)
TEAM = {"Progetti": "progetti"}


def replay(lines, held_open=False, settle_each=False):
    """
    Feeds the lines in order, ticking at each line's time. With `held_open` every write
    stays open until the end, as the macOS client does for about 35 s; otherwise a write
    is closed at the next tick.
    """
    t, at = fa.Tracker(), None
    events = []
    for line in lines:
        at, op = fa.parse(line, at)
        if op is None:
            continue
        t.feed(at, op)
        events += t.tick(at, set(t.pending) if held_open else set())
    events += t.tick(at + 60, set())
    events += t.tick(at + 120, set())
    return events


def rows(events, by_folder=TEAM):
    return [(r[2], r[3], r[4], r[5]) for r in fa.to_rows(events, by_folder, lambda p: None)]


class Parse(unittest.TestCase):
    def test_prefixed_line(self):
        at, op = fa.parse(
            "[2026/10/19 06:45:48.720674,  1]   Floppy|renameat|ok|/mnt/drive/a|b|/mnt/drive/c\n",
            None,
        )
        self.assertEqual(op, ["renameat", "/mnt/drive/a|b", "/mnt/drive/c"])
        self.assertAlmostEqual(at % 60, 48.720674, places=5)

    def test_header_on_its_own_line(self):
        at, op = fa.parse(
            "[2026/10/19 06:45:48.5,  1] ../../source3/modules/vfs_full_audit.c:1(do_log)\n", None
        )
        self.assertIsNone(op)
        at2, op = fa.parse("  Floppy|unlinkat|ok|/mnt/drive/x\n", at)
        self.assertEqual((at2, op), (at, ["unlinkat", "/mnt/drive/x"]))

    def test_failures_and_other_ops_are_skipped(self):
        self.assertIsNone(
            fa.parse(
                "[2026/10/19 06:45:48.5,  1]   Floppy|unlinkat|fail (No such file)|/mnt/drive/x\n",
                None,
            )[1]
        )
        self.assertIsNone(
            fa.parse("[2026/10/19 06:45:48.5,  1]   Floppy|openat|ok|r|/mnt/drive/x\n", None)[1]
        )


class Fixture(unittest.TestCase):
    def test_closed_at_once(self):
        self.assertEqual(
            rows(replay(FIXTURE)),
            [
                ("created", "folder", "Shoot", None),
                ("added", "file", "Shoot/clip.mov", None),
                ("written", "file", "Shoot/project.json", None),
                ("replaced", "file", "Shoot/project.json", None),
                ("written", "file", "Shoot/project.json", None),
                ("replaced", "file", "Shoot/clip.mov", None),
                ("renamed", "file", "Shoot/clip.mov", "Shoot/take1.mov"),
                ("created", "folder", "Shoot/Raw", None),
                ("written", "file", "Shoot/Raw/a.mov", None),
                ("written", "file", "Shoot/Raw/b.mov", None),
                ("renamed", "folder", "Shoot", "Shoot 2"),
                ("deleted", "file", "Shoot 2/take1.mov", None),
                ("deleted", "file", "Shoot 2/Raw/a.mov", None),
                ("deleted", "file", "Shoot 2/Raw/b.mov", None),
                ("deleted", "folder", "Shoot 2/Raw", None),
            ],
        )

    def test_held_open_like_the_mac(self):
        # The clip copied, replaced, renamed and deleted before its first close is one new
        # file that went away; the three saves of project.json are one, under the folder's
        # new name; the files made and deleted inside Raw still show.
        got = rows(replay(FIXTURE, held_open=True))
        self.assertNotIn("replaced", [r[0] for r in got])
        self.assertEqual(
            [r for r in got if "take1" in f"{r[2]} {r[3]}" or "project" in r[2]],
            [
                ("renamed", "file", "Shoot/clip.mov", "Shoot/take1.mov"),
                ("added", "file", "Shoot 2/take1.mov", None),
                ("deleted", "file", "Shoot 2/take1.mov", None),
                ("written", "file", "Shoot 2/project.json", None),
            ],
        )
        self.assertIn(("written", "file", "Shoot 2/Raw/a.mov", None), got)
        self.assertIn(("renamed", "folder", "Shoot", "Shoot 2"), got)

    def test_single_user_volume(self):
        got = rows(replay(FIXTURE), by_folder=None)
        self.assertEqual(got[0], ("created", "folder", "Progetti/Shoot", None))

    def test_outside_known_disks(self):
        self.assertEqual(rows(replay(FIXTURE), by_folder={"Altro": "altro"}), [])


class Renames(unittest.TestCase):
    def feed(self, *ops):
        t = fa.Tracker()
        for i, op in enumerate(ops):
            t.feed(100.0 + i * 0.1, op)
            t.tick(100.0 + i * 0.1, set(t.pending))
        return rows(t.tick(200, set()) + t.tick(300, set()))

    def test_hidden_temp_to_visible_name(self):
        got = self.feed(
            ["create_file", "0x60087", "file", "create", "/mnt/drive/Progetti/.clip.mov.part"],
            ["renameat", "/mnt/drive/Progetti/.clip.mov.part", "/mnt/drive/Progetti/clip.mov"],
        )
        self.assertEqual(got, [("added", "file", "clip.mov", None)])

    def test_closed_temp_renamed_later(self):
        t = fa.Tracker()
        t.feed(100.0, ["create_file", "0x60087", "file", "create", "/mnt/drive/Progetti/.x.tmp"])
        events = t.tick(100.5, set())
        t.feed(101.0, ["renameat", "/mnt/drive/Progetti/.x.tmp", "/mnt/drive/Progetti/x"])
        events += t.tick(200, set())
        self.assertEqual(rows(events), [("added", "file", "x", None)])

    def test_across_disks_is_not_a_rename(self):
        got = self.feed(["renameat", "/mnt/drive/Progetti/a", "/mnt/drive/Altro/a"])
        self.assertEqual(got, [])


class Store(unittest.TestCase):
    def test_list_after_and_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            fa.DB = Path(d) / "activity.db"
            db = fa.connect()
            self.addCleanup(db.close)
            r = fa.to_rows(replay(FIXTURE), TEAM, lambda p: 3)
            sql = (
                "INSERT OR IGNORE INTO events (at, disk, op, kind, path, dest, size)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
            )
            with db:
                db.executemany(sql, r)
                db.executemany(sql, r)  # a replay of the same lines
            first = fa.list_events(0, 5)
            self.assertEqual(len(first["events"]), 5)
            rest = fa.list_events(first["last"], 1000)
            self.assertEqual(len(rest["events"]), len(r) - 5)
            self.assertEqual(
                rest["events"][-1],
                {**rest["events"][-1], "op": "deleted", "kind": "folder", "disk": "progetti"},
            )
            self.assertNotIn("size", rest["events"][-1])
            self.assertEqual(first["events"][1]["size"], 3)
            json.dumps(first)


class Follow(unittest.TestCase):
    def test_rotation_and_half_lines(self):
        with tempfile.TemporaryDirectory() as d:
            fa.AUDIT = Path(d) / "audit.log"
            fa.AUDIT.write_text("a\nb")
            f = fa.Follower(None, 0)
            self.assertEqual(f.lines(), ["a\n"])
            with open(fa.AUDIT, "a") as w:
                w.write("\nc\n")
            os.rename(fa.AUDIT, str(fa.AUDIT) + ".old")
            fa.AUDIT.write_text("d\n")
            self.assertEqual(f.lines(), ["b\n", "c\n", "d\n"])
            inode, offset = f.position()
            f.close()
            with open(fa.AUDIT, "a") as w:
                w.write("e\n")
            g = fa.Follower(inode, offset)
            self.assertEqual(g.lines(), ["e\n"])
            g.close()

    def test_restart_resumes_in_the_rotated_file(self):
        with tempfile.TemporaryDirectory() as d:
            fa.AUDIT = Path(d) / "audit.log"
            fa.AUDIT.write_text("a\nb\n")
            f = fa.Follower(None, 0)
            f.lines()
            inode, _ = f.position()
            f.close()
            os.rename(fa.AUDIT, str(fa.AUDIT) + ".old")
            fa.AUDIT.write_text("c\n")
            g = fa.Follower(inode, 2)
            self.assertEqual(g.lines(), ["b\n", "c\n"])
            g.close()


class Trash(unittest.TestCase):
    """The trash on a plain folder laid out like JuiceFS's, inodes made up."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.disk = Path(d.name)
        (self.disk / "Cantine").mkdir()
        (self.disk / ".config").write_text(json.dumps({"Format": {"TrashDays": 7}}))
        hour = self.disk / ".trash" / "2026-10-19-07"
        hour.mkdir(parents=True)
        # Cantine is inode 10. Girato (20) was deleted with a.mov and Raw (21) with b.mov.
        for name, body in [
            ("10-20-Girato", None),
            ("20-30-a.mov", "aaaa"),
            ("20-21-Raw", None),
            ("21-31-b.mov", "bb"),
            ("10-32-musica.xlsx", "v1"),
            ("99-33-orphan.txt", "o"),
        ]:
            if body is None:
                (hour / name).mkdir()
            else:
                (hour / name).write_text(body)
        (self.disk / "Cantine" / "musica.xlsx").write_text("v2")
        self.paths = {10: "Cantine"}
        self.mount = fa.Mount("progetti", "Progetti", str(self.disk), True)

    def where(self, _mount, ino):
        return self.paths.get(ino)

    @staticmethod
    def move(src, dst):
        if os.path.lexists(dst):
            raise FileExistsError(dst)
        os.rename(src, dst)

    def listing(self):
        entries = fa.scan_trash(str(self.disk))
        return fa.list_trash(self.mount, entries, self.where, lambda *_: False)

    def test_list_folds_a_deleted_folder(self):
        got = {e["name"]: e for e in self.listing()["entries"]}
        self.assertEqual(sorted(got), ["Girato", "musica.xlsx", "orphan.txt"])
        self.assertEqual((got["Girato"]["files"], got["Girato"]["size"]), (2, 6))
        self.assertEqual(got["Girato"]["from"], "Cantine")
        self.assertTrue(got["musica.xlsx"]["replaced"])
        self.assertFalse(got["Girato"]["replaced"])
        self.assertIsNone(got["orphan.txt"]["from"])
        self.assertEqual(got["Girato"]["expires_at"], timegm((2026, 10, 19, 7, 0, 0)) + 7 * 86400)

    def test_restore_a_folder_with_its_files(self):
        out = fa.restore(
            self.mount, "2026-10-19-07/10-20", fa.scan_trash(str(self.disk)), self.where, self.move
        )
        self.assertEqual(out["path"], "Cantine/Girato")
        self.assertEqual((self.disk / "Cantine/Girato/a.mov").read_text(), "aaaa")
        self.assertEqual((self.disk / "Cantine/Girato/Raw/b.mov").read_text(), "bb")
        self.assertEqual(
            sorted(e["name"] for e in self.listing()["entries"]), ["musica.xlsx", "orphan.txt"]
        )

    def test_restore_never_writes_over_a_file(self):
        entries = fa.scan_trash(str(self.disk))
        out = fa.restore(self.mount, "2026-10-19-07/10-32", entries, self.where, self.move)
        self.assertEqual((out["name"], out["renamed"]), ("musica (ripristinato).xlsx", True))
        self.assertEqual((self.disk / "Cantine/musica.xlsx").read_text(), "v2")
        self.assertEqual((self.disk / "Cantine/musica (ripristinato).xlsx").read_text(), "v1")

    def test_restore_without_its_folder_goes_to_the_top(self):
        out = fa.restore(
            self.mount, "2026-10-19-07/99-33", fa.scan_trash(str(self.disk)), self.where, self.move
        )
        self.assertEqual((out["path"], out["moved"]), ("orphan.txt", True))

    def test_read_only_and_unknown(self):
        entries = fa.scan_trash(str(self.disk))
        ro = fa.Mount("archivio", "Archivio", str(self.disk), False)
        with self.assertRaises(PermissionError):
            fa.restore(ro, "2026-10-19-07/10-32", entries, self.where, self.move)
        with self.assertRaises(FileNotFoundError):
            fa.restore(self.mount, "2026-10-19-07/10-99", entries, self.where, self.move)

    def test_free_names(self):
        self.assertEqual(fa.free_name("a.tar.gz", 2), "a.tar (ripristinato 2).gz")
        self.assertEqual(fa.free_name("Girato", 1), "Girato (ripristinato)")
        self.assertEqual(fa.free_name(".hidden", 1), ".hidden (ripristinato)")


if __name__ == "__main__":
    unittest.main(verbosity=1)
