"""I2 — closure: nothing in the frame that the reference did not paint.

`docs/001-spec/textual-migration.md` §4.2. A compositor swap has one failure
mode that matters: Textual's own chrome drawing in its own colours, or a widget
reaching for a default instead of a theme slot.

Both halves are asserted twice: once against the reference, which needs nothing
installed beyond pyte, and once against the Textual candidate. The reference
half is what stays meaningful on a bare install, and it is also the control that
says the candidate half is not passing vacuously.

Nothing is recorded to disk. The reference is captured live on every run and
the candidate is held to exactly what it painted — and to the rows it painted,
cell for cell (`TestCandidateMatchesReference`).
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
    "textual missing: pip install -e .")

#: The two sizes worth paying a subprocess for. 100x30 is where the §8.3 bars
#: fit and the frame paints 50 colours; 80x24 is the commonest terminal and the
#: one where the bars do not fit. One of each catches the two regimes.
#: The candidate halves below run as a smoke (distinct at 80x24) unless
#: `HUEBOX_ALL=1` restores the full matrix — the reference halves stay full
#: because they are in-process draws and cost nothing.
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
                raw, height = harness.capture_reference(fixture, cols, rows)
                with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                    self._assert_backed(raw, cols, rows, height, "reference")

    @needs_pyte
    @needs_candidate
    def test_the_candidate_frame_is_backed(self):
        cases = [(cols, rows, fixture)
                 for cols, rows in SIZES
                 for fixture in sorted(harness.FIXTURES)]
        if not os.environ.get("HUEBOX_ALL"):
            cases = [case for case in cases
                     if case == (80, 24, "distinct")]
        for cols, rows, fixture in cases:
            _, height = harness.capture_reference(fixture, cols, rows)
            raw = harness.candidate_bytes(fixture, cols, rows)
            with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                self._assert_backed(raw, cols, rows, height, "candidate")


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
                raw, height = harness.capture_reference(
                    "distinct", cols, rows)
                grid = harness.parse(raw, cols, rows, height)
                top = "".join(cell[0] for cell in grid[0])
                self.assertIn("huebox", top,
                              "the wordmark scrolled off the top")


class TestClosure(unittest.TestCase):
    """Every colour in the frame is one the reference painted."""

    def _assert_closed(self, raw, cols, rows, height, allowed, label):
        found = set(harness.closure(raw, cols, rows, height))
        self.assertEqual(sorted(found - allowed), [],
                         "%s: colours the reference never painted" % label)

    def _reference_closure(self, fixture, cols, rows):
        raw, height = harness.capture_reference(fixture, cols, rows)
        return raw, height, set(harness.closure(raw, cols, rows, height))

    @needs_pyte
    @needs_candidate
    def test_the_candidate_stays_inside_the_reference_closure(self):
        """The point of I2. A Textual surface that fell through to its own
        palette — a scrollbar thumb, a focus ring, `$text`'s `ansi_default` —
        lands here and nowhere else."""
        cases = [(cols, rows, fixture)
                 for cols, rows in SIZES
                 for fixture in sorted(harness.FIXTURES)]
        if not os.environ.get("HUEBOX_ALL"):
            cases = [case for case in cases
                     if case == (80, 24, "distinct")]
        for cols, rows, fixture in cases:
            _, height, allowed = self._reference_closure(
                fixture, cols, rows)
            raw = harness.candidate_bytes(fixture, cols, rows)
            with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                self._assert_closed(raw, cols, rows, height,
                                    allowed, "candidate")

    @needs_pyte
    def test_closure_is_wider_than_the_slots_at_a_size_where_bars_fit(self):
        """The closure is captured, never hand-listed — and it is bigger than
        the 22 slots. If it ever collapsed to exactly the slots, the §8.3 bar
        sweeps stopped painting and this fixture stopped seeing them."""
        raw, height = harness.capture_reference("distinct", 100, 30)
        found = set(harness.closure(raw, 100, 30, height))
        slots = set(harness.FIXTURES["distinct"]["slots"].values())
        normalised = {value.lstrip("#").lower() for value in slots}
        outside = [c for c in found if c not in normalised]
        self.assertTrue(outside,
                        "expected Pygments default and bar sweeps at 100x30")
        # Pygments' default style, because render.py calls lex() with no style=
        self.assertIn("ffffff", found)
        self.assertIn("000000", found)


if __name__ == "__main__":
    unittest.main()


@needs_pyte
@needs_candidate
class TestCandidateMatchesReference(unittest.TestCase):
    """The compositor is faithful: the app paints what the editor wrote.

    Live fidelity without goldens. The reference is captured fresh on every run
    and the candidate — held bare (`HUEBOX_PANELS=0`, `HUEBOX_COLLAPSIBLE=0`)
    so both sides draw the same rows — must match it cell for cell: char, fg,
    bg and attrs. `test_render`/`test_editor` say the bytes are right; this says
    the compositor kept them that way. A swapped pair of slots, a widget
    reaching for the wrong slot, a depth degradation: all pass I2's set check
    and all fail here.
    """

    def _compare(self, fixture, cols, rows, sel=0):
        from unittest import mock
        raw, height = harness.capture_reference(fixture, cols, rows, sel)
        expected = harness.parse(raw, cols, rows, height)
        # Bare rows: panels and collapsibles are product chrome, so the
        # candidate is pinned bare here while I2 runs product.
        with mock.patch.dict(os.environ, {"HUEBOX_PANELS": "0",
                                          "HUEBOX_COLLAPSIBLE": "0"}):
            candidate = harness.candidate_bytes(fixture, cols, rows, sel)
        actual = harness.parse(candidate, cols, rows, height)
        differences = [(y, x, expected[y][x], actual[y][x])
                       for y in range(height)
                       for x in range(cols)
                       if expected[y][x] != actual[y][x]]
        self.assertEqual(actual, expected,
                         "frame changed:\n" + _report(differences))

    def test_every_fixture_matches_at_every_size(self):
        """A smoke by default, the matrix on demand.

        A launch costs a Python start plus a settled frame, so the default is
        one launch (distinct at 80x24, the commonest terminal);
        `HUEBOX_ALL=1` restores the full 4x3 matrix for a release.
        """
        if not os.environ.get("HUEBOX_ALL"):
            tiers = [("distinct", 80, 24)]
        else:
            tiers = [(fixture, cols, rows)
                     for cols, rows in harness.SIZES
                     for fixture in sorted(harness.FIXTURES)]
        for fixture, cols, rows in tiers:
            with self.subTest(fixture=fixture, size=f"{cols}x{rows}"):
                self._compare(fixture, cols, rows)

    def test_selection_at_each_grid_edge(self):
        """The selection walks, and the compositor carries it.

        The arrows move which cell is bold, and that is a function of the grid,
        not of the size, so one size is enough and the process is worth it.
        Smoke is one edge; `HUEBOX_ALL=1` walks both.
        """
        sels = (0, 21)
        if not os.environ.get("HUEBOX_ALL"):
            sels = (0,)
        for sel in sels:
            with self.subTest(sel=sel):
                self._compare("distinct", 80, 24, sel)


def _report(differences):
    lines = []
    for y, x, was, now in differences[:12]:
        lines.append("  row %-3d col %-3d  %s -> %s" % (y, x, was, now))
    if len(differences) > 12:
        lines.append("  ... and %d more" % (len(differences) - 12))
    return "\n".join(lines)


@needs_pyte
@needs_candidate
class TestPickerClosure(unittest.TestCase):
    """I2 for the picker.

    A scrollbar is the single most likely place for a new colour source in this
    whole codebase: seven of Textual's 168 design tokens exist only for it.

    The `*` and `>` rows carry no colour of their own — the picker's colours are
    the theme's name in the foreground — so the closure is small and a scrollbar
    thumb would stand out in it immediately.
    """

    def _assert_closed(self, raw, cols, rows, height, allowed, label):
        found = set(harness.closure(raw, cols, rows, height))
        self.assertEqual(sorted(found - allowed), [],
                         "%s: colours the picker never painted" % label)

    def _reference_closure(self, fixture, cols, rows, scene):
        raw, height = harness.capture_reference_picker(
            fixture, cols, rows, scene)
        return raw, height, set(harness.closure(raw, cols, rows, height))

    def test_the_candidate_picker_stays_inside_the_reference_closure(self):
        cases = [(cols, rows, fixture, scene)
                 for cols, rows in SIZES
                 for fixture in sorted(harness.FIXTURES)
                 for scene in sorted(harness.PICKERS)]
        if not os.environ.get("HUEBOX_ALL"):
            cases = [case for case in cases
                     if case == (80, 24, "distinct", "long")]
        for cols, rows, fixture, scene in cases:
            _, height, allowed = self._reference_closure(
                fixture, cols, rows, scene)
            raw = harness.candidate_picker_bytes(fixture, cols, rows,
                                                  scene)
            with self.subTest(fixture=fixture, size=f"{cols}x{rows}",
                              picker=scene):
                self._assert_closed(raw, cols, rows, height,
                                    allowed, "candidate")

    def test_the_picker_has_no_colour_a_scrollbar_could_join(self):
        """The closure today is the theme's own foreground and background.

        Stated as a fact about today rather than a rule to keep — a future
        scrollbar that is genuinely coloured from the theme is fine, and I2 is
        what would have to accept it.
        """
        raw, height = harness.capture_reference_picker(
            "distinct", 100, 30, "long")
        recorded = set(harness.closure(raw, 100, 30, height))
        # slots carry their `#`; the closure is read out of a terminal, which
        # does not. Compared raw, this is the emptiest assertion in the file.
        slots = {value.lstrip("#")
                 for value in harness.FIXTURES["distinct"]["slots"].values()}
        self.assertTrue(recorded <= slots,
                        "the picker paints a colour no slot holds: %s"
                        % sorted(recorded - slots))
        self.assertGreaterEqual(len(recorded), 2,
                                "an empty closure would pass the check above")

    def test_the_picker_header_survives_a_library_that_fills_the_screen(self):
        """The picker's trailing-newline defect, as a regression guard.

        At 60x16 with 34 themes the frame filled the screen, the trailing CRLF
        scrolled the terminal, and the top row — the picker saying what it
        is — fell off. Same defect as the editor frame's, fixed in the overlay
        branch the same way: withhold the newline when the rows fill the
        screen.
        """
        raw, height = harness.capture_reference_picker(
            "distinct", 60, 16, "long")
        self.assertEqual(height, 16, "the frame must fill it")
        grid = harness.parse(raw, 60, 16, height)
        top = "".join(cell[0] for cell in grid[0])
        self.assertIn("themes", top, "the picker's header scrolled off")
