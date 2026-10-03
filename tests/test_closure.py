"""I2 — closure: nothing in the frame that the reference did not paint.

`docs/001-spec/textual-migration.md` §4.2. I1 says the frame did not change; I2
says a fourth colour source did not arrive. Between them they catch the two
failure modes a compositor swap actually has: Textual's own chrome drawing in
its own colours, and a widget reaching for a default instead of a theme slot.

Both halves are asserted twice: once against the reference, which needs nothing
installed beyond pyte, and once against the Textual candidate. The reference
half is what stays meaningful on a bare install, and it is also the control that
says the candidate half is not passing vacuously.
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
needs_candidate = unittest.skipUnless(
    harness.candidate_available(),
    "textual missing: pip install -e '.[editor]'")

#: The two sizes worth paying a subprocess for. 100x30 is where the §8.3 bars
#: fit and the frame paints 50 colours; 80x24 is the commonest terminal and the
#: one where the bars do not fit. One of each catches the two regimes.
SIZES = ((100, 30), (80, 24))


class TestBackdropHolds(unittest.TestCase):
    """§8.2: no column of the frame shows the terminal's background.

    A reset reopens the fill, so a row is repainted after every SGR 0 inside it.
    This is the property a compositor swap is most likely to break, because the
    swap replaces the mechanism that produced it — and it costs nothing to hold
    it the whole way.
    """

    def _assert_backed(self, raw, cols, rows, height, label):
        holes = harness.unbacked(raw, cols, rows, height)
        self.assertEqual(holes[:10], [],
                         "%s: %d cells show the terminal's background"
                         % (label, len(holes)))

    @needs_pyte
    def test_the_reference_frame_is_backed(self):
        for cols, rows in SIZES:
            for fixture in sorted(harness.FIXTURES):
                golden = harness.load(fixture, cols, rows)
                raw, _ = harness.capture_reference(fixture, cols, rows)
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    self._assert_backed(raw, cols, rows,
                                        golden["frame_rows"], "reference")

    @needs_pyte
    @needs_candidate
    def test_the_candidate_frame_is_backed(self):
        for cols, rows in SIZES:
            for fixture in sorted(harness.FIXTURES):
                golden = harness.load(fixture, cols, rows)
                raw = harness.candidate_bytes(fixture, cols, rows)
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    self._assert_backed(raw, cols, rows,
                                        golden["frame_rows"], "candidate")


class TestClosure(unittest.TestCase):
    """Every colour in the frame is one the reference painted."""

    def _assert_closed(self, raw, cols, rows, height, allowed, label):
        found = set(harness.closure(raw, cols, rows, height))
        self.assertEqual(sorted(found - allowed), [],
                         "%s: colours the reference never painted" % label)

    @needs_pyte
    def test_the_reference_stays_inside_its_own_closure(self):
        """The control: if the reference itself failed this, the candidate's
        passing would mean nothing."""
        for cols, rows in SIZES:
            for fixture in sorted(harness.FIXTURES):
                golden = harness.load(fixture, cols, rows)
                raw, _ = harness.capture_reference(fixture, cols, rows)
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    self._assert_closed(raw, cols, rows, golden["frame_rows"],
                                        set(golden["closure"]), "reference")

    @needs_pyte
    @needs_candidate
    def test_the_candidate_stays_inside_the_recorded_closure(self):
        """The point of I2. A Textual surface that fell through to its own
        palette — a scrollbar thumb, a focus ring, `$text`'s `ansi_default` —
        lands here and nowhere else."""
        for cols, rows in SIZES:
            for fixture in sorted(harness.FIXTURES):
                golden = harness.load(fixture, cols, rows)
                raw = harness.candidate_bytes(fixture, cols, rows)
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    self._assert_closed(raw, cols, rows, golden["frame_rows"],
                                        set(golden["closure"]), "candidate")

    @needs_pyte
    def test_closure_is_wider_than_the_slots_at_a_size_where_bars_fit(self):
        """The closure is captured, never hand-listed — and it is bigger than
        the 22 slots. If it ever collapsed to exactly the slots, the §8.3 bar
        sweeps stopped painting and this fixture stopped seeing them."""
        golden = harness.load("distinct", 100, 30)
        slots = set(harness.FIXTURES["distinct"]["slots"].values())
        normalised = {value.lstrip("#").lower() for value in slots}
        outside = [c for c in golden["closure"] if c not in normalised]
        self.assertTrue(outside,
                        "expected Pygments default and bar sweeps at 100x30")
        # Pygments' default style, because render.py calls lex() with no style=
        self.assertIn("ffffff", golden["closure"])
        self.assertIn("000000", golden["closure"])


if __name__ == "__main__":
    unittest.main()
