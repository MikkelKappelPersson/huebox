"""I2 — closure: nothing in the frame that the reference did not paint.

`docs/001-spec/textual-migration.md` §4.2. I1 says the frame did not change; I2
says a fourth colour source did not arrive. Between them they catch the two
failure modes a compositor swap actually has: Textual's own chrome drawing in
its own colours, and a widget reaching for a default instead of a theme slot.
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


@needs_pyte
class TestBackdropHolds(unittest.TestCase):
    """§8.2: no column of the frame shows the terminal's background.

    A reset reopens the fill, so a row is repainted after every SGR 0 inside it.
    This is the property a compositor swap is most likely to break, because the
    swap replaces the mechanism that produced it — and it costs nothing to hold
    it the whole way.
    """

    def test_no_cell_in_the_frame_is_unbacked(self):
        for cols, rows in harness.SIZES:
            for fixture in sorted(harness.FIXTURES):
                golden = harness.load(fixture, cols, rows)
                raw = harness.candidate_bytes(fixture, cols, rows)
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    holes = harness.unbacked(raw, cols, rows,
                                             golden["frame_rows"])
                    self.assertEqual(holes[:10], [],
                                     "%d cells show the terminal's background"
                                     % len(holes))


@needs_pyte
class TestClosure(unittest.TestCase):
    """Every colour in the frame is one the reference painted."""

    def test_candidate_stays_inside_the_recorded_closure(self):
        for cols, rows in harness.SIZES:
            for fixture in sorted(harness.FIXTURES):
                golden = harness.load(fixture, cols, rows)
                raw = harness.candidate_bytes(fixture, cols, rows)
                allowed = set(golden["closure"])
                found = set(harness.closure(raw, cols, rows,
                                            golden["frame_rows"]))
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    self.assertEqual(sorted(found - allowed), [],
                                     "colours the reference never painted")

    def test_closure_is_wider_than_the_slots_at_a_size_where_bars_fit(self):
        """The closure is captured, never hand-listed — and it is bigger than
        the 22 slots. If it ever collapses to exactly the slots, the §8.3 bar
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

    def test_dark_fixture_is_not_satisfied_by_the_background_alone(self):
        """The near-black fixture exists to catch a leaked default.

        Its foregrounds are #030303..#060606 against a #010101 background, so a
        frame painted in a widget's own default rather than the theme's shows up
        as a colour no slot holds.
        """
        golden = harness.load("dark", 100, 30)
        normalised = {"ffffff", "000000"}
        outside = [c for c in golden["closure"]
                   if c not in normalised
                   and c != "010101"
                   and c not in {"030303", "020202", "040404", "050505",
                                 "060606"}]
        # The bar sweeps interpolate, so outside holds computed colours; the
        # point is only that the fixture painted more than its own background.
        self.assertNotEqual(outside, [])


if __name__ == "__main__":
    unittest.main()