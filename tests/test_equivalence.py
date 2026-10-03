"""I1 — the candidate frame equals the reference, cell for cell.

`docs/001-spec/textual-migration.md` §4.1, §4.4 and §4.5. Phase 0 wires I1
against the reference itself, so it is green before any migration code exists;
`harness.candidate_bytes` is the one seam that phase 2 replaces.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: `import harness`

import harness  # noqa: E402

needs_pyte = unittest.skipUnless(
    harness.pyte is not None, "pyte missing: pip install -e '.[test]'")


class TestGoldenPresence(unittest.TestCase):
    """A golden that does not exist must fail, never be re-recorded.

    §4.4: the reference is captured once, before the migration starts. If a
    missing golden silently regenerated itself, the comparison would be against
    whatever the code does today and the whole promise collapses.
    """

    def test_every_fixture_and_size_has_a_golden(self):
        missing = [name
                   for cols, rows in harness.SIZES
                   for name in sorted(harness.FIXTURES)
                   if harness.load(name, cols, rows) is None]
        self.assertEqual(missing, [],
                         "missing goldens — run: python3 tests/harness.py --record")

    def test_goldens_are_not_empty(self):
        for cols, rows in harness.SIZES:
            for name in sorted(harness.FIXTURES):
                golden = harness.load(name, cols, rows)
                with self.subTest(fixture=name, size=f"{cols}x{rows}"):
                    self.assertTrue(golden["cells"], "empty frame")
                    self.assertTrue(golden["closure"], "no colours recorded")


@needs_pyte
class TestFrameGeometry(unittest.TestCase):
    """The frame paints every row it wrote, and keeps its top row.

    §4.8: when the frame filled the screen, the trailing newline scrolled it and
    the top row — the `huebox` wordmark — fell off. This is the regression guard
    for that, and it deliberately asserts *no rows lost* rather than a height
    formula: the frame's height is content-dependent, so the only durable claim
    is that what was written is what is on screen.
    """

    def test_the_frame_paints_every_row_it_wrote(self):
        for fixture in sorted(harness.FIXTURES):
            for cols, rows in harness.SIZES:
                raw, written = harness.capture_reference(fixture, cols, rows)
                extent = harness.painted_extent(raw, cols, rows)
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    self.assertIsNotNone(extent, "frame painted nothing")
                    first, last, count = extent
                    self.assertEqual(first, 0,
                                     "frame does not start at row 0 — it "
                                     "scrolled")
                    self.assertEqual(last, written - 1,
                                     "painted rows are not contiguous")
                    self.assertEqual(count, written,
                                     "wrote %d rows, painted %d: a row is lost"
                                     % (written, count))

    def test_the_wordmark_survives_at_the_size_that_used_to_lose_it(self):
        """80x24 is the case the defect hit, and the commonest terminal there
        is. Asserting the top row's own text is stronger than counting rows."""
        for cols, rows in ((80, 24), (40, 12)):
            with self.subTest(size=f"{cols}x{rows}"):
                grid = harness.record("distinct", cols, rows)["cells"]
                top = "".join(cell[0] for cell in grid[0])
                self.assertIn("huebox", top,
                              "the wordmark scrolled off the top")


@needs_pyte
class TestEquivalence(unittest.TestCase):
    """I1 proper: the candidate grid is the golden grid."""

    def _compare(self, fixture, cols, rows, sel=0):
        golden = harness.load(fixture, cols, rows, sel)
        self.assertIsNotNone(golden, "no golden recorded")
        raw = harness.candidate_bytes(fixture, cols, rows, sel)
        actual = harness.parse(raw, cols, rows, golden["frame_rows"])
        self.assertEqual(actual, golden["cells"],
                         "frame changed:\n" + self._report(
                             harness.diff(golden["cells"], actual, cols)))
        return raw

    def _report(self, differences):
        lines = []
        for y, x, was, now in differences[:12]:
            lines.append("  row %-3d col %-3d  %s -> %s" % (y, x, was, now))
        if len(differences) > 12:
            lines.append("  ... and %d more" % (len(differences) - 12))
        return "\n".join(lines)

    def test_every_fixture_matches_at_every_size(self):
        for cols, rows in harness.SIZES:
            for fixture in sorted(harness.FIXTURES):
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    self._compare(fixture, cols, rows)

    def test_selection_at_each_grid_edge(self):
        # 0 and 21 are the first and last of the 22 slots (§5); a move that
        # repainted the wrong cell would show here and nowhere else.
        for sel in (0, 21):
            for cols, rows in harness.SIZES:
                with self.subTest(sel=sel, size=f"{cols}x{rows}"):
                    golden = harness.record("distinct", cols, rows, sel)
                    raw = harness.candidate_bytes("distinct", cols, rows, sel)
                    actual = harness.parse(raw, cols, rows,
                                           golden["frame_rows"])
                    self.assertEqual(actual, golden["cells"])


@needs_pyte
class TestDepthProbe(unittest.TestCase):
    """§4.5 — why the depth probe exists, asserted so it cannot be forgotten.

    `pyte` collapses `38;5;196` and `38;2;255;0;0` to the same hex, so a
    candidate that degraded to the 256-colour palette would be invisible to I1
    on a truecolor terminal and visibly wrong on a 256-colour host. This test
    demonstrates the blind spot; it is the reason `candidate_bytes` takes an
    environment, and the reason phase 2 runs every fixture at both depths.
    """

    def test_pyte_cannot_see_a_palette_degradation(self):
        screen_true = harness.pyte.Screen(10, 1)
        harness.pyte.ByteStream(screen_true).feed(
            b"\033[38;2;255;0;0mX")
        screen_256 = harness.pyte.Screen(10, 1)
        harness.pyte.ByteStream(screen_256).feed(b"\033[38;5;196mX")

        self.assertEqual(screen_true.buffer[0][0].fg, "ff0000")
        self.assertEqual(screen_256.buffer[0][0].fg, "ff0000",
                         "pyte normalised 196 — if this ever differs, §4.5's "
                         "blind spot has closed and the probe can be revisited")

    def test_depth_environments_are_distinguishable(self):
        truecolor = harness.color_depth_env("truecolor")
        plain = harness.color_depth_env("256")
        self.assertNotEqual(truecolor["COLORTERM"], plain["COLORTERM"])
        self.assertEqual(truecolor["TERM"], plain["TERM"])

    def test_unknown_depth_is_rejected(self):
        with self.assertRaises(ValueError):
            harness.color_depth_env("cmyk")


def _sgr(hexvalue, background=False):
    r, g, b = (int(hexvalue[i:i + 2], 16) for i in (0, 2, 4))
    return "\033[%s;%d;%d;%dm" % ("48;2" if background else "38;2", r, g, b)


@needs_pyte
class TestHarnessHasTeeth(unittest.TestCase):
    """The harness must be able to fail, or it proves nothing.

    In phase 0 the candidate *is* the reference, so I1 is green by construction.
    That is the intended order — a green test that has never seen the migration
    is what makes the later green tests mean something — but only if the
    comparison is capable of noticing a difference at all. So: mutate the real
    frame, and require every check to notice.
    """

    def _mutate_colour(self, cols, rows, replacement):
        golden = harness.load("distinct", cols, rows)
        raw, _ = harness.capture_reference("distinct", cols, rows)
        text = raw.decode("utf-8")
        for victim in golden["closure"]:
            mutated = text.replace(_sgr(victim), _sgr(replacement))
            if mutated != text:
                return golden, mutated.encode("utf-8"), victim
        self.fail("no foreground colour in the frame could be mutated")

    def test_equivalence_notices_a_recoloured_cell(self):
        golden, mutated, victim = self._mutate_colour(100, 30, "123456")
        actual = harness.parse(mutated, 100, 30, golden["frame_rows"])
        self.assertNotEqual(actual, golden["cells"],
                            "I1 did not notice a recoloured cell")
        differences = harness.diff(golden["cells"], actual, 100)
        self.assertTrue(differences, "diff() reported nothing")
        row, col, was, now = differences[0]
        self.assertEqual(now[1], "123456",
                         "diff() named the wrong colour")
        self.assertNotEqual(was[1], now[1], "diff() compared a cell to itself")
        self.assertTrue(victim)

    def test_closure_notices_a_colour_the_reference_never_painted(self):
        golden, mutated, _ = self._mutate_colour(100, 30, "123456")
        allowed = set(golden["closure"])
        found = set(harness.closure(mutated, 100, 30,
                                    golden["frame_rows"]))
        self.assertEqual(sorted(found - allowed), ["123456"],
                         "I2 let an unknown colour through")

    def test_backdrop_check_notices_an_unpainted_cell(self):
        """Truncating the frame leaves the tail unpainted, which is exactly the
        failure §8.2's reset-reopen-fill prevents."""
        golden = harness.load("distinct", 100, 30)
        raw, _ = harness.capture_reference("distinct", 100, 30)
        self.assertEqual(harness.unbacked(raw, 100, 30,
                                          golden["frame_rows"]), [])
        cut = raw[:len(raw) // 2]
        self.assertTrue(harness.unbacked(cut, 100, 30,
                                         golden["frame_rows"]),
                        "the backdrop check cannot see an unpainted cell")

    def test_diff_names_cells_and_colours(self):
        """The golden diff is the review artefact (§4.4), so it has to point at
        a coordinate and show both colours, not report inequality."""
        golden = harness.load("distinct", 80, 24)
        actual = [[list(cell) for cell in row] for row in golden["cells"]]
        # recolour the first cell of row 3
        actual[3][0][1] = "abcdef"
        differences = harness.diff(golden["cells"], actual, 80)
        self.assertTrue(differences)
        row, col, was, now = differences[0]
        self.assertEqual(row, 3)
        self.assertEqual(col, 0)
        self.assertEqual(now[1], "abcdef")
        self.assertNotEqual(was[1], now[1])


if __name__ == "__main__":
    unittest.main()