#!/usr/bin/env python3
"""
Tests of macos/vm/guest/floppy-notify: Samba's watch list, Valkey's pub/sub replies, and
the folders' listings turned into added, removed and modified, on a plain temp folder.

  python3 macos/tests/notify_test.py
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
here = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader(
    "floppy_notify", str(here / "../vm/guest/floppy-notify")
)
spec = importlib.util.spec_from_loader("floppy_notify", loader)
fn = importlib.util.module_from_spec(spec)
sys.modules["floppy_notify"] = fn
loader.exec_module(fn)


class Watches(unittest.TestCase):
    def test_smbstatus_notify_lines(self):
        # As Samba 4.17 printed it in the spike, after the locked files.
        out = (
            "Locked files:\n193  0  DENY_NONE  0x100081  RDONLY  NONE  /mnt/drive  finder-live\n\n"
            "/mnt/drive/Progetti/finder-live\\193\\d47\\0\n"
            "/mnt/drive/Progetti/Cantine Arnaldi\\193\\d47\\0\n"
            "/tmp/elsewhere\\12\\1\\0\n"
        )
        self.assertEqual(
            fn.watched(out),
            {"/mnt/drive/Progetti/finder-live", "/mnt/drive/Progetti/Cantine Arnaldi"},
        )


class Protocol(unittest.TestCase):
    def test_commands_and_replies_in_pieces(self):
        self.assertEqual(fn.resp("AUTH", "u", "p"), b"*3\r\n$4\r\nAUTH\r\n$1\r\nu\r\n$1\r\np\r\n")
        r = fn.Reader()
        msg = fn.resp("pmessage", "__keyspace@1__:d*", "__keyspace@1__:d2049", "hset")
        r.feed(b"+OK\r\n" + msg[:30])
        self.assertEqual(r.replies(), [b"+OK"])
        r.feed(msg[30:] + b"*3\r\n$10\r\npsubscribe\r\n")
        got = r.replies()
        self.assertEqual(
            got, [[b"pmessage", b"__keyspace@1__:d*", b"__keyspace@1__:d2049", b"hset"]]
        )
        self.assertEqual(fn.KEY.match(got[0][2]).groups(), (b"d", b"2049"))
        self.assertIsNone(fn.KEY.match(b"__keyspace@1__:dirUsedSpace"))


class Folders(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name, "Girato")
        self.dir.mkdir()
        (self.dir / "A013.mov").write_text("x")
        self.sent = []
        self.w = fn.Watcher(lambda action, path: self.sent.append((action, os.path.basename(path))))
        self.w.watch({str(self.dir)})
        self.ino = os.stat(self.dir).st_ino

    def test_a_colleague_adds_renames_and_deletes(self):
        (self.dir / "A014.mov").write_text("y")
        os.rename(self.dir / "A013.mov", self.dir / "A013 bis.mov")
        self.w.event(b"d", self.ino, 100.0)
        self.w.event(b"d", self.ino, 100.1)  # the same burst
        self.w.settle(100.2)
        self.assertEqual(self.sent, [])  # still settling
        self.w.settle(100.4)
        self.assertEqual(
            self.sent, [("removed", "A013.mov"), ("added", "A013 bis.mov"), ("added", "A014.mov")]
        )

    def test_a_file_growing_is_modified_every_two_seconds(self):
        clip = os.stat(self.dir / "A013.mov").st_ino
        for t in (10.0, 10.5, 11.0, 12.1):
            self.w.event(b"i", clip, t)
        self.assertEqual(self.sent, [("modified", "A013.mov"), ("modified", "A013.mov")])
        self.w.event(b"i", 999_999, 20.0)  # a file in no open window
        self.assertEqual(len(self.sent), 2)

    def test_a_window_closed_is_forgotten(self):
        self.w.watch(set())
        self.w.event(b"d", self.ino, 1.0)
        self.w.settle(5.0)
        self.assertEqual((self.sent, self.w.files), ([], {}))

    def test_a_replaced_file_is_removed_and_added(self):
        self.assertEqual(
            fn.diff({"a": 1, "b": 2}, {"a": 3, "b": 2}), [("removed", "a"), ("added", "a")]
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
