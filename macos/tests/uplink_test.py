#!/usr/bin/env python3
"""
Tests of macos/vm/guest/floppy-uplink's rate against a simulated line: one router queue of a
given speed and depth, fed by the shaped rate, pinged twice a tick. The real lines are in
deploy/local/upload-bench.py; these check the controller's arithmetic in seconds.

  python3 macos/tests/uplink_test.py
"""

import importlib.machinery
import importlib.util
import statistics
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
here = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader(
    "floppy_uplink", str(here / "../vm/guest/floppy-uplink")
)
spec = importlib.util.spec_from_loader("floppy_uplink", loader)
fu = importlib.util.module_from_spec(spec)
sys.modules["floppy_uplink"] = fu
loader.exec_module(fu)

BASE_MS = 20.0


class Line:
    """A router in front of a line of `mbps`, with `buffer_s` seconds of queue."""

    def __init__(self, mbps: float, buffer_s: float) -> None:
        self.mbps, self.buffer_s = mbps, buffer_s
        self.queue = 0.0  # Mbit waiting in the router

    def tick(self, offered: float) -> tuple[float, list[float]]:
        """What left the VM in Mbit/s, and the round trips seen during the tick."""
        cap = self.mbps * self.buffer_s
        queue = self.queue + (offered - self.mbps) * fu.TICK
        sent = offered
        if queue > cap:
            # A full queue drops: TCP gets the line and what the queue still had room for.
            sent = self.mbps + (cap - self.queue) / fu.TICK
            queue = cap
        self.queue = max(0.0, queue)
        delay = BASE_MS + self.queue / self.mbps * 1000
        return sent, [delay, delay + 0.4]


def run(  # noqa: PLR0917 (the simulation's knobs, all named at the call sites that change them)
    rate: "fu.Rate",
    line: Line,
    seconds: float,
    demand: float = 5000.0,
    t0: float = 0.0,
    quiet: float = 5.0,
):
    """Ticks a copy that could send `demand` Mbit/s after `quiet` seconds of an idle line, as at
    the VM's start; each tick's (rate, sent, delay above base) during the copy."""
    t = t0
    while t < t0 + quiet:
        _, rtts = line.tick(0.0)
        rate.step(rtts, 0.0, t)
        t += fu.TICK
    t0 = t
    out = []
    while t < t0 + seconds:
        sent, rtts = line.tick(min(rate.rate, demand))
        rate.step(rtts, sent, t)
        out.append((rate.rate, sent, min(rtts) - BASE_MS))
        t += fu.TICK
    return out


def tail(trace, seconds: float):
    return trace[-int(seconds / fu.TICK) :]


class Converges(unittest.TestCase):
    def assert_settled(self, trace, mbps: float):
        last = tail(trace, 30)
        sent = statistics.mean(s for _, s, _ in last)
        delays = sorted(d for _, _, d in last)
        p95 = delays[int(len(delays) * 0.95) - 1]
        self.assertGreaterEqual(sent, 0.8 * mbps, f"uses {sent:.1f} of {mbps}")
        self.assertLessEqual(sent, 1.0 * mbps + 0.01)
        self.assertLess(p95, 150, f"waits {p95:.0f} ms behind the uploads")

    def test_the_line_that_broke_a_copy(self):
        # 40 Mbit/s with 3 s of router buffer (CYCLES 15).
        self.assert_settled(run(fu.Rate(50), Line(40, 3.0), 90), 40)

    def test_a_slow_office_uplink(self):
        self.assert_settled(run(fu.Rate(50), Line(10, 2.0), 90), 10)

    def test_a_fast_line_is_reached(self):
        trace = run(fu.Rate(50), Line(500, 0.1), 60)
        self.assert_settled(trace, 500)
        reached = next(i for i, (_, s, _) in enumerate(trace) if s >= 0.8 * 500) * fu.TICK
        self.assertLess(reached, 15, "a fast line is reached within seconds")

    def test_a_rate_learned_on_a_faster_line_comes_down(self):
        # The saved rate of a gigabit office, then a 40 Mbit/s line: it used to stay up.
        trace = run(fu.Rate(1849), Line(40, 3.0), 90)
        self.assert_settled(trace, 40)
        down = next(i for i, (r, _, _) in enumerate(trace) if r <= 45) * fu.TICK
        self.assertLess(down, 15)

    def test_the_line_slows_down_mid_copy(self):
        rate = fu.Rate(50)
        line = Line(40, 3.0)
        run(rate, line, 60)
        line.mbps = 10
        self.assert_settled(run(rate, line, 60, t0=70, quiet=0), 10)


class Holds(unittest.TestCase):
    def test_no_upload_no_change(self):
        # Round trips grow from someone else's traffic (a download's queue) while nothing is
        # uploaded: not this VM's queue to drain.
        rate = fu.Rate(40)
        for i in range(40):
            rate.step([BASE_MS + (300 if i > 10 else 0)], 0.2, i * fu.TICK)
        self.assertEqual(rate.rate, 40)

    def test_idle_keeps_the_rate(self):
        rate = fu.Rate(40)
        for i in range(100):
            rate.step([BASE_MS], 0.0, i * fu.TICK)
        self.assertEqual(rate.rate, 40)

    def test_bounds(self):
        rate = fu.Rate(10_000, max_mbps=4000)
        self.assertEqual(rate.rate, 4000)
        rate = fu.Rate(0.1)
        self.assertEqual(rate.rate, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
