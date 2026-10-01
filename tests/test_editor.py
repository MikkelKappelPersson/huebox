"""Editor tests: the too-small floor, frame determinism, staged saves (§15, §14)."""

import io
import os
import re
import signal
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)                   # tests dir: cross-test imports

from huebox import editor  # noqa: E402
from huebox.color import NAMED, SLOTS  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FULL_SLOTS = {name: "#3f7a3f" for name in SLOTS}
HINT = ("terminal too small — need "
        f"{editor.MIN_COLS}x{editor.MIN_ROWS}")
SAVE = editor.SAVE_KEY


def frame(cols, rows, sel=3, undo=(), status="", mult=1, slots=None):
    """draw_editor's output at a chosen size, captured as a string."""
    out = io.StringIO()
    with mock.patch.object(editor, "term_size", return_value=(cols, rows)), \
            mock.patch.object(sys, "stdout", out):
        editor.draw_editor("ghostty", "/tmp/huebox.conf",
                           FULL_SLOTS if slots is None else slots, sel,
                           list(undo), status, mult)
    return out.getvalue()


def lines(text):
    return text.split("\r\n")[:-1]        # the frame ends with a newline


def width(line):
    return len(ANSI.sub("", line))


def example_rows(body):
    """How many of the strip's three rows this frame drew."""
    return sum(1 for line in body if re.match(
        r"^  (background|selection-background|cursor-color)/", line))


def _code_rows(body):
    start = next((i for i, line in enumerate(body) if "live code" in line), None)
    if start is None:
        return []
    out = []
    for line in body[start + 1:]:
        if not line.startswith("    "):        # the block is over
            break
        out.append(line)
    return out


def code_lines(body):
    """How many rows of the code sample this frame drew."""
    return len(_code_rows(body))


def code_block(body):
    """The code sample's own lines, plain: no header row, no blank ones."""
    return [ANSI.sub("", line).strip() for line in _code_rows(body)
            if line.strip()]


class TooSmall(unittest.TestCase):
    def test_hint_text_and_centering(self):
        self.assertEqual(editor.too_small_frame(120).strip(), HINT)
        centred = editor.too_small_frame(80)
        self.assertTrue(centred.startswith(" " * ((80 - len(HINT)) // 2)))
        self.assertEqual(len(ANSI.sub("", editor.too_small_frame(20))), 20)

    def test_hint_replaces_the_frame(self):
        # the FULL hint must survive at MIN_COLS-1: a truncated hint would
        # hide the very size it tells the user to enlarge to (review P1)
        for size in ((editor.MIN_COLS - 1, 24), (80, editor.MIN_ROWS - 1)):
            with self.subTest(size=size):
                out = frame(*size)
                self.assertIn(HINT, out)
                self.assertNotIn("palette", out)
                self.assertNotIn("\033[48;2;", out)   # no swatches drawn

    def test_frame_is_one_line_on_a_cleared_screen(self):
        sizes = ((editor.MIN_COLS - 1, 24), (60, 8), (24, 10), (10, 4))
        for cols, rows in sizes:
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertEqual(len(body), 1)
                self.assertTrue(body[0].startswith("\033[H\033[2J"))
                self.assertLessEqual(width(body[0]), cols)

    def test_at_the_minimum_the_editor_draws(self):
        out = frame(editor.MIN_COLS, editor.MIN_ROWS)
        self.assertNotIn("too small", out)
        self.assertIn("palette", out)


class NormalFrame(unittest.TestCase):
    def test_eight_by_twentyfour_is_the_full_editor(self):
        out = frame(80, 24, status="saved")
        self.assertNotIn("too small", out)
        self.assertIn("huebox", out)
        self.assertIn("palette", out)
        self.assertIn("interface", out)
        self.assertIn("saved", out)
        self.assertLessEqual(len(lines(out)), 24)

    def test_frames_are_byte_identical(self):
        args = dict(sel=17, undo=[("palette-3", "#ffffff")], status="saved",
                    mult=5)
        self.assertEqual(frame(80, 24, **args), frame(80, 24, **args))
        self.assertEqual(frame(60, 16, **args), frame(60, 16, **args))

    def test_layout_holds_at_several_sizes(self):
        # (40, 10) is below-min on rows and belongs to the TooSmall tests
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            with self.subTest(size=(cols, rows)):
                for line in lines(frame(cols, rows)):
                    self.assertLessEqual(width(line), cols)

    def test_a_wider_terminal_spreads_the_palette(self):
        narrow = max(width(line) for line in lines(frame(60, 16)))
        wide = max(width(line) for line in lines(frame(100, 30)))
        self.assertGreater(wide, narrow)

    def test_frame_follows_the_buffer_not_disk(self):
        # §14.1 — the frame renders unsaved state: one buffer mutation with
        # no save in between must change what is drawn
        before = frame(80, 24)
        edited = dict(FULL_SLOTS, background="#101014", foreground="#e6e6ea")
        after = frame(80, 24, slots=edited)
        self.assertNotEqual(before, after)
        self.assertIn("#101014", ANSI.sub("", after))
        self.assertNotIn("#101014", ANSI.sub("", before))
        # and the same buffer twice is still byte-identical
        self.assertEqual(after, frame(80, 24, slots=edited))

    def test_examples_sit_above_the_code_sample(self):
        body = lines(frame(100, 30))
        rows = {ANSI.sub("", line).split()[0] if ANSI.sub("", line).split()
                else "" for line in body}
        self.assertIn("examples", rows)
        self.assertIn("live", rows)
        self.assertLess(body.index(next(l for l in body if "examples" in l)),
                        body.index(next(l for l in body if "live code" in l)))

    def test_examples_and_the_block_share_the_leftover_rows(self):
        # §15 — the strip gives up rows before the code block loses a line,
        # and both survive at every size spec §15.4 tests
        for cols, rows, strip, code in ((100, 30, 3, 7), (80, 24, 3, 4),
                                        (60, 24, 3, 4)):
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertEqual(example_rows(body), strip)
                self.assertEqual(code_lines(body), code)

    def test_the_block_outlives_the_strip(self):
        # the sample is the widget the editor exists to show (§9), so where
        # the two cannot both fit the strip gives up rows first — and goes
        # before the block does, including at the sizes where the block
        # used to vanish entirely
        for cols, rows, strip, code in ((80, 20, 3, 3), (80, 18, 1, 3),
                                        (80, 16, 0, 3), (60, 20, 0, 4)):
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertEqual(example_rows(body), strip)
                self.assertEqual(code_lines(body), code)

    def test_a_truncated_block_drops_its_least_useful_lines(self):
        # a short frame spends rows on code: the leading comment, the
        # closing brace and the blank inside the block go first
        block = code_block(lines(frame(80, 24)))
        self.assertTrue(block[0].startswith("const huebox"))
        self.assertNotIn("}", block)
        self.assertFalse(any("live preview" in line for line in block))

    def test_the_frame_sheds_decoration_before_a_widget(self):
        # §15 — the palette legend is a courtesy (the grid is numbered), so
        # a tight frame spends it, and the blank separators after it, before
        # it spends a widget
        legend = ANSI.sub("", editor.PALETTE_LEGEND)
        tall = [ANSI.sub("", line) for line in lines(frame(80, 30))]
        self.assertIn(legend, tall)
        tight = [ANSI.sub("", line) for line in lines(frame(80, 24))]
        self.assertNotIn(legend, tight)
        for keep in ("palette", "interface", "AaBbCc", "examples", "live code"):
            self.assertTrue(any(keep in line for line in tight), keep)

    def test_no_size_overflows_its_rows(self):
        for cols, rows in ((120, 44), (100, 40), (100, 30), (80, 30), (80, 28),
                           (80, 24), (80, 22), (80, 20), (80, 18), (80, 16),
                           (80, 14), (60, 24), (60, 20), (60, 16), (60, 14)):
            with self.subTest(size=(cols, rows)):
                body = lines(frame(cols, rows))
                self.assertLessEqual(len(body), rows)
                self.assertTrue(all(width(line) <= cols for line in body))


class EditLoop(unittest.TestCase):
    """The loop itself: draw / read / apply, and only save writes (§14.2)."""

    def run_session(self, keys, path=None, slots=None, cols=None):
        """Drive `edit()` with a scripted key stream; return the writes."""
        writes, draws, handlers = [], [], []

        def record(name, target, values):
            # §13.7 — the writer is handed the subject every save, because
            # the picker can move it mid-session; a legacy session has none
            writes.append((name, target, dict(values)))

        def note_handler(*args, **kwargs):
            draws.append({"slots": dict(args[2]), "status": args[5],
                          "head": kwargs.get("head"),
                          "overlay": kwargs.get("overlay"),
                          "grid": kwargs.get("grid")})
            handlers.append(signal.getsignal(signal.SIGWINCH))

        stream = iter(keys)
        target = path or os.path.join(self.tmp.name, "kitty.conf")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("# colours\n")
        with mock.patch.object(editor, "enter_raw", return_value=(7, None)),\
                mock.patch.object(editor, "exit_raw"),\
                mock.patch.object(editor, "draw_editor",
                                  side_effect=note_handler),\
                mock.patch.object(editor, "read_key",
                                  side_effect=lambda fd: next(stream)),\
                mock.patch.object(editor, "term_size",
                                  return_value=(cols or 80, 24)),\
                mock.patch.object(sys, "stdout", io.StringIO()):
            editor.edit("kitty", target, dict(slots or FULL_SLOTS), record)
        return writes, draws, handlers, target

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_resize_redraws_without_consuming_a_key_or_writing(self):
        before = signal.getsignal(signal.SIGWINCH)
        writes, draws, handlers, _ = self.run_session(["resize", "f", "esc"])
        self.assertEqual(writes, [])        # §14.2: nothing but Ctrl+S writes
        self.assertEqual(len(draws), 3)      # the resize redrew, ate no key
        self.assertEqual(handlers, [editor._on_winch] * 3)
        self.assertEqual(signal.getsignal(signal.SIGWINCH), before)
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp.name, "kitty.conf.huebox.bak")))

    def test_the_loop_draws_from_the_buffer_and_saves_once(self):
        writes, draws, _, path = self.run_session(["w", SAVE, "esc"])
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:2], (None, path))   # direct mode: no theme
        # the frame drawn after "w" already carried the adjusted value,
        # before any save happened (palette-0 is the selected slot)
        self.assertNotEqual(draws[0]["slots"]["palette-0"],
                            draws[1]["slots"]["palette-0"])
        self.assertEqual(draws[1]["slots"]["palette-0"],
                         writes[0][2]["palette-0"])

    def test_the_head_names_the_config_in_a_direct_session(self):
        _, draws, _, path = self.run_session(["esc"])
        # §13.7 — `direct:<path>` says a save writes the config itself,
        # and the dim path is dropped so it is not printed twice
        self.assertEqual(draws[0]["head"], f"direct:{path}")
        self.assertIsNone(draws[0]["overlay"])

    def test_a_dirty_quit_takes_two_escapes(self):
        writes, draws, _, _ = self.run_session(["w", "esc", "esc"])
        self.assertEqual(writes, [])        # discarded, not saved
        self.assertIn("unsaved changes", draws[-1]["status"])
        self.assertEqual(len(draws), 3)      # armed frame is drawn once

    def test_the_frame_and_the_keys_are_given_one_geometry(self):
        # §4.3 / §15.2 — the grid the loop computes per frame is the one the
        # frame draws and the arrows walk, so the two cannot disagree
        for cols in (80, 60, 40):
            writes, draws, _, _ = self.run_session(["esc"], cols=cols)
            self.assertEqual(writes, [])
            self.assertTrue(draws)
            for draw in draws:
                self.assertEqual(draw["grid"], editor.grid_geometry(cols))

    def test_the_keys_follow_the_terminal_the_frame_is_drawn_for(self):
        # 40 columns puts four swatches to a row, so one arrow down is
        # palette-4 — not the palette-8 a fixed eight-slot stride would pick
        writes, _, _, _ = self.run_session(["down", "s", SAVE, "esc"], cols=40)
        self.assertEqual(len(writes), 1)
        self.assertNotEqual(writes[0][2]["palette-4"], FULL_SLOTS["palette-4"])
        self.assertEqual(writes[0][2]["palette-8"], FULL_SLOTS["palette-8"])


class ApplyKey(unittest.TestCase):
    """`apply_key` on its own: no terminal, no loop, just state (§14.2)."""

    def state(self, prompt=None, backup=None, slots=None, sel=0):
        writes = []
        st = editor.EditorState(slots or dict(FULL_SLOTS),
                                lambda values: writes.append(dict(values)),
                                prompt, backup)
        st.sel = sel
        return st, writes

    def background_state(self, **kwargs):
        """A state with the `background` slot selected, so nudges show."""
        return self.state(sel=SLOTS.index("background"), **kwargs)

    def dirty_and_adjust(self, st, key="w"):
        name = SLOTS[st.sel]
        start = st.slots[name]
        editor.apply_key(key, st)
        self.assertNotEqual(st.slots[name], start)
        return start

    def test_adjust_marks_dirty_and_writes_nothing(self):
        st, writes = self.background_state()
        self.dirty_and_adjust(st)
        self.assertTrue(st.dirty())
        self.assertFalse(st.written)
        self.assertEqual(writes, [])

    def test_save_writes_once_and_cleans_the_buffer(self):
        st, writes = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key(SAVE, st)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0], dict(st.slots))
        self.assertFalse(st.dirty())
        self.assertTrue(st.written)
        self.assertEqual(st.status, "saved")
        self.assertEqual(st.saved, dict(st.slots))

    def test_every_buffer_edit_is_written_only_on_save(self):
        st, writes = self.background_state()
        for key in ("w", "w", "a", "x"):
            editor.apply_key(key, st)
        self.assertEqual(writes, [])
        editor.apply_key(SAVE, st)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["background"], st.slots["background"])

    def test_dirty_esc_arms_and_armed_esc_quits(self):
        st, _ = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key("esc", st)
        self.assertTrue(st.armed)
        self.assertFalse(st.quit)
        self.assertIn("discard", st.status)
        editor.apply_key("esc", st)
        self.assertTrue(st.quit)

    def test_another_key_disarms(self):
        st, _ = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key("esc", st)
        editor.apply_key("f", st)
        self.assertFalse(st.armed)
        self.assertEqual(st.status, "step size x5")
        editor.apply_key("esc", st)         # still dirty: it arms again
        self.assertTrue(st.armed)
        self.assertFalse(st.quit)

    def test_a_clean_esc_quits_at_once(self):
        st, _ = self.state()
        editor.apply_key("esc", st)
        self.assertTrue(st.quit)
        self.assertFalse(st.armed)
        self.assertEqual(st.status, "")

    def test_ctrl_c_takes_the_esc_path(self):
        st, _ = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key("\x03", st)
        self.assertTrue(st.armed)
        self.assertFalse(st.quit)
        editor.apply_key("\x03", st)
        self.assertTrue(st.quit)
        clean, _ = self.state()
        editor.apply_key("\x03", clean)
        self.assertTrue(clean.quit)

    def test_revert_goes_to_the_last_save_not_session_start(self):
        st, writes = self.background_state()
        start = dict(st.slots)
        self.dirty_and_adjust(st)           # dirty
        editor.apply_key(SAVE, st)          # checkpoint
        checkpoint = dict(st.slots)
        self.assertNotEqual(checkpoint, start)
        self.dirty_and_adjust(st)           # dirty again, past the save
        editor.apply_key("r", st)
        self.assertEqual(st.slots, checkpoint)
        self.assertEqual(st.saved, checkpoint)
        self.assertNotEqual(st.slots, start)
        self.assertFalse(st.dirty())
        self.assertEqual(st.undo, [])
        self.assertEqual(st.status, "reverted to last save")
        self.assertEqual(len(writes), 1)

    def test_revert_before_any_save_returns_to_the_start(self):
        st, writes = self.background_state()
        self.dirty_and_adjust(st)
        editor.apply_key("r", st)
        self.assertEqual(st.slots, FULL_SLOTS)
        self.assertEqual(st.status, "reverted to start")
        self.assertEqual(writes, [])

    def test_undo_survives_a_save(self):
        st, writes = self.background_state()
        start = self.dirty_and_adjust(st)
        middle = st.slots["background"]
        self.dirty_and_adjust(st)
        editor.apply_key(SAVE, st)          # saving does not clear the log
        self.assertEqual(len(st.undo), 2)
        editor.apply_key("u", st)
        self.assertEqual(st.slots["background"], middle)
        editor.apply_key("u", st)
        self.assertEqual(st.slots["background"], start)
        self.assertEqual(st.undo, [])
        self.assertEqual(len(writes), 1)

    def test_hex_entry_goes_through_the_prompt_callback(self):
        asked = []

        def prompt(name):
            asked.append(name)
            return "#AbCdEf"

        st, writes = self.background_state(prompt=prompt)
        self.dirty_and_adjust(st)
        editor.apply_key("i", st)
        # the prompt carries the slot, not just a bare name (P5 review)
        self.assertEqual(asked, ["  new hex for background: "])
        self.assertEqual(st.slots["background"], "#abcdef")
        self.assertEqual(len(st.undo), 2)   # the nudge plus the typed hex
        self.assertEqual(st.status, "background = #abcdef")
        editor.apply_key("u", st)
        self.assertEqual(st.status, "undid background")
        self.assertEqual(writes, [])
        editor.apply_key("X", st)
        self.assertEqual(asked[-1], "  new hex for background: ")

    def test_hex_entry_rejects_junk_and_cancellation(self):
        st, _ = self.background_state(prompt=lambda name: "not a colour")
        self.dirty_and_adjust(st)
        before = dict(st.slots)
        editor.apply_key("i", st)
        self.assertEqual(st.slots, before)
        self.assertIn("valid", st.status)

        cancelled, _ = self.state(prompt=lambda name: None)
        editor.apply_key("i", cancelled)
        self.assertEqual(cancelled.status, "")
        self.assertFalse(cancelled.dirty())

    def test_without_a_prompt_callback_hex_entry_is_a_no_op(self):
        st, _ = self.background_state()
        self.dirty_and_adjust(st)
        before = dict(st.slots)
        editor.apply_key("i", st)
        self.assertEqual(st.slots, before)

    def test_movement_and_step_size_stay_clean(self):
        st, writes = self.state()
        editor.apply_key("down", st)
        self.assertEqual(SLOTS[st.sel], "palette-8")
        editor.apply_key("up", st)
        self.assertEqual(st.sel, 0)
        editor.apply_key("right", st)
        self.assertEqual(st.sel, 1)
        editor.apply_key("left", st)
        editor.apply_key("f", st)
        self.assertEqual(st.mult, 5)
        self.assertFalse(st.dirty())
        self.assertEqual(writes, [])

    def test_an_absent_slot_is_not_adjustable(self):
        partial = {"palette-0": "#123456"}
        st, writes = self.state(slots=partial)
        editor.apply_key("right", st)       # palette-1: no value in this file
        editor.apply_key("w", st)
        editor.apply_key("i", st)
        self.assertEqual(st.slots, partial)
        editor.apply_key(SAVE, st)
        self.assertEqual(writes, [partial])
        self.assertFalse(st.dirty())
        editor.apply_key("esc", st)
        self.assertTrue(st.quit)            # Esc still quits


class GridArrows(unittest.TestCase):
    """The arrows walk the grid the frame draws, row for row (§4.3).

    The old movement was a fixed slot stride, which only ever matched the
    80-column frame: the six interface cells are drawn as three rows of two
    and were stepped through as a single list, so left/right read as "next
    in the list" and down fell off the end of it.
    """

    def land(self, name, *keys, cols=80):
        """The slot `keys` reach from `name`, in the grid at `cols`."""
        index = SLOTS.index(name)
        grid = editor.grid_geometry(cols)
        for key in keys:
            index = editor.move_slot(index, key, grid)
        return SLOTS[index]

    def swatch_rows(self, cols, rows=16):
        """How many swatches the frame actually put on each palette row."""
        raw = lines(frame(cols, rows, sel=0))
        start = next(i for i, line in enumerate(raw)
                     if ANSI.sub("", line).strip() == "palette")
        out = []
        for line in raw[start + 1:]:
            if "48;2;" not in line:         # the escapes count the cells
                break
            out.append(line.count("48;2;"))
        return out

    def marked(self, name, cols, rows=16):
        """A frame with `name` selected, as plain text."""
        return "".join(ANSI.sub("", line) for line in lines(
            frame(cols, rows, sel=SLOTS.index(name))))

    def mark(self, name):
        """How the frame prints `>` on a palette swatch (`> 8 `, `>12 `)."""
        return f">{int(name.split('-')[-1]):>2} "

    def test_the_interface_grid_is_three_rows_of_two(self):
        self.assertEqual((editor.grid_geometry(80).named_cols, len(NAMED)),
                         (2, 6))
        # right crosses the pair; down walks the column it is in
        self.assertEqual(self.land("background", "right"), "foreground")
        self.assertEqual(self.land("foreground", "left"), "background")
        self.assertEqual(self.land("background", "down"), "cursor-color")
        self.assertEqual(self.land("background", "down", "down"),
                         "selection-background")
        self.assertEqual(self.land("foreground", "down"), "cursor-text")
        self.assertEqual(self.land("cursor-text", "down"),
                         "selection-foreground")
        # down and right from one cell meet at the same place as the other
        self.assertEqual(self.land("background", "down", "right"),
                         self.land("background", "right", "down"))

    def test_left_and_right_stay_in_their_row(self):
        self.assertEqual(self.land("cursor-text", "left"), "cursor-color")
        # a row's edge is the frame's edge — no wrap into the next row
        self.assertEqual(self.land("foreground", "right"), "foreground")
        self.assertEqual(self.land("cursor-color", "left"), "cursor-color")

    def test_down_crosses_from_the_palette_into_the_interface(self):
        # the palette's bottom row sits directly above the interface's first
        self.assertEqual(self.land("palette-8", "down"), "background")
        self.assertEqual(self.land("palette-9", "down"), "foreground")

    def test_up_crosses_back_out_of_the_interface(self):
        self.assertEqual(self.land("background", "up"), "palette-8")
        self.assertEqual(self.land("foreground", "up"), "palette-9")
        self.assertEqual(self.land("cursor-color", "up"), "background")

    def test_the_outer_ends_of_the_frame_stay_put(self):
        self.assertEqual(self.land("palette-0", "up"), "palette-0")
        self.assertEqual(self.land("palette-0", "left"), "palette-0")
        self.assertEqual(self.land("selection-foreground", "down"),
                         "selection-foreground")
        self.assertEqual(self.land("selection-foreground", "right"),
                         "selection-foreground")

    def test_a_narrow_palette_row_is_one_key_not_two(self):
        # 40 columns puts four swatches to a row: down is the cell below
        # the selection, not the cell two rows down
        grid = editor.grid_geometry(40)
        self.assertEqual((grid.palette_cols, grid.named_cols), (4, 1))
        self.assertEqual(self.land("palette-0", "down", cols=40), "palette-4")
        self.assertEqual(self.land("palette-3", "down", cols=40), "palette-7")
        self.assertEqual(self.land("palette-12", "down", cols=40), "background")
        self.assertEqual(self.land("background", "up", cols=40), "palette-12")

    def test_a_one_column_interface_still_steps_a_row(self):
        # two interface cells need 68 columns; below that there is one to a
        # row — the list order — and down is the only way along it
        self.assertEqual(editor.grid_geometry(80).named_cols, 2)
        self.assertEqual(editor.grid_geometry(60).named_cols, 1)
        self.assertEqual(self.land("background", "down", cols=60), "foreground")
        self.assertEqual(self.land("background", "down", "down", cols=60),
                         "cursor-color")
        self.assertEqual(self.land("background", "right", cols=60),
                         "background")        # one cell to a row: no across

    def test_the_frame_and_the_keys_agree_on_the_grid(self):
        for cols in (80, 60, 40):
            grid = editor.grid_geometry(cols)
            self.assertEqual(set(self.swatch_rows(cols)),
                             {grid.palette_cols}, cols)
            # the `>` the frame draws lands on the slot the arrows reach
            above = self.land("background", "up", cols=cols)
            self.assertNotEqual(above, "palette-0")
            self.assertNotIn(self.mark(above), self.marked("palette-0", cols))
            self.assertIn(self.mark(above), self.marked(above, cols))

    def test_the_key_surface_moves_by_the_frame_geometry(self):
        # apply_key, not move_slot: the arrows as the loop actually feeds them
        st = editor.EditorState(dict(FULL_SLOTS), lambda values: None)
        st.grid = editor.grid_geometry(80)
        st.sel = SLOTS.index("background")
        for key, expected in (("down", "cursor-color"),
                              ("down", "selection-background"),
                              ("right", "selection-foreground"),
                              ("up", "cursor-text"),
                              ("up", "foreground")):
            editor.apply_key(key, st)
            self.assertEqual(SLOTS[st.sel], expected)
        self.assertFalse(st.dirty())        # navigation writes nothing


class Backup(unittest.TestCase):
    """`<path>.huebox.bak` moves from editor-open to first-save (§14.2)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "kitty.conf")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("background #101014\n")
        self.backup = self.path + ".huebox.bak"

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_first_save_snapshots_the_pre_save_file(self):
        st, writes = self.state()
        editor.apply_key(SAVE, st)
        self.assertEqual(len(writes), 1)
        self.assertTrue(os.path.exists(self.backup))
        self.assertEqual(self.read(self.backup), "background #101014\n")
        self.assertTrue(st.backup_made)

    def test_the_backup_is_never_overwritten(self):
        st, _ = self.state()
        editor.apply_key(SAVE, st)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("background #000000\n")     # something else rewrote it
        editor.apply_key("w", st)
        editor.apply_key(SAVE, st)
        self.assertEqual(self.read(self.backup), "background #101014\n")

    def test_edits_alone_never_create_a_backup(self):
        st, _ = self.state()
        for key in ("w", "a", "x", "s"):
            editor.apply_key(key, st)
        editor.apply_key("esc", st)
        editor.apply_key("esc", st)
        self.assertFalse(os.path.exists(self.backup))

    def test_a_session_that_never_saves_leaves_no_backup(self):
        st, _ = self.state()
        editor.apply_key("esc", st)
        self.assertFalse(os.path.exists(self.backup))
        self.assertFalse(st.written)

    def test_theme_files_get_no_backup(self):
        writes = []
        theme = editor.EditorState(dict(FULL_SLOTS),
                                   lambda values: writes.append(dict(values)),
                                   None, None)      # huebox owns it: no .bak
        editor.apply_key(SAVE, theme)
        self.assertEqual(len(writes), 1)
        self.assertFalse(os.path.exists(self.backup))
        self.assertFalse(theme.backup_made)

    def state(self):
        writes = []
        return editor.EditorState(dict(FULL_SLOTS),
                                  lambda values: writes.append(dict(values)),
                                  None, self.path), writes


class ThemeSession(unittest.TestCase):
    """Theme mode: same keys, truth file, no `.bak`, push report on
    stderr (§13.2, §13.6)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.theme = os.path.join(self.tmp.name, "ember.toml")
        with open(self.theme, "w", encoding="utf-8") as handle:
            handle.write('[colors]\nbackground = "#000000"\n')

    def session(self, keys, report=None):
        writes = []
        stream = iter(keys)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(editor, "enter_raw", return_value=(7, None)),\
                mock.patch.object(editor, "exit_raw"),\
                mock.patch.object(editor, "term_size",
                                  return_value=(100, 30)),\
                mock.patch.object(editor, "read_key",
                                  side_effect=lambda fd: next(stream)),\
                mock.patch.object(sys, "stdout", out),\
                mock.patch.object(sys, "stderr", err):
            editor.edit("ghostty", self.theme, dict(FULL_SLOTS),
                        lambda name, path, values: writes.append(
                            (name, path, dict(values))),
                        backup=False, theme="ember", report=report)
        return writes, out.getvalue(), err.getvalue()

    def test_saving_writes_the_truth_file_and_nothing_else(self):
        writes, out, err = self.session(["w", SAVE, "esc"])
        self.assertEqual(len(writes), 1)
        # the writer is told the subject, so a picker switch retargets it
        self.assertEqual(writes[0][:2], ("ember", self.theme))
        self.assertNotEqual(writes[0][2]["palette-0"],
                            FULL_SLOTS["palette-0"])
        # huebox owns the file: no pre-save backup, and no claim about the
        # terminal — the writer's push report says what reached it (§13.6)
        self.assertFalse(os.path.exists(self.theme + ".huebox.bak"))
        self.assertIn("saved theme ember", out)
        self.assertNotIn("reload your terminal", out)
        self.assertNotIn("backup of the pre-save state", out)
        self.assertEqual(err, "")

    def test_the_push_report_is_stderr_not_frame_noise(self):
        # printed after raw mode is over, so a save can say what it pushed
        # without the report scrolling through the editor frame (§13.6)
        report = ["ghostty: pushed to /tmp/config", "reload your terminal"]
        _, out, err = self.session(["w", SAVE, "esc"], report)
        self.assertIn("saved theme ember", out)
        self.assertNotIn("pushed to", out)
        self.assertEqual(err.splitlines(),
                         ["huebox: ghostty: pushed to /tmp/config",
                          "huebox: reload your terminal"])

    def test_a_theme_session_that_saves_nothing_says_so(self):
        _, out, _ = self.session(["esc"])
        self.assertIn("no changes", out)

    def test_the_head_names_the_theme_and_the_dirty_dot(self):
        _, out, _ = self.session(["w", "esc", "esc"])
        # §13.7 — `ember ghostty` while clean, a `●` between them once the
        # buffer differs from the last save
        plain = [ANSI.sub("", frame)
                 for frame in out.split("\033[H\033[2J")]
        self.assertTrue(any("  ember ghostty" in frame for frame in plain))
        self.assertTrue(any(f"  ember {editor.DIRTY_MARK} ghostty" in frame
                            for frame in plain))
        self.assertTrue(any("/tmp" in frame or "ember.toml" in frame
                            for frame in plain))


def theme_slots(seed: int) -> dict:
    """A recognisable slot dict per theme, so a switch cannot hide behind a
    load that returns what was already in the buffer."""
    return dict(FULL_SLOTS, background=f"#{seed:02x}2f4f",
                foreground=f"#{seed:02x}bad0")


class FakeLibrary:
    """The three library calls the picker makes, with no disk behind them.

    `problem` is what `create` complains with, `misses` the names `load`
    cannot open — the two failure paths that have to stay on the status bar
    instead of raising into the draw loop.
    """

    def __init__(self, names=("ash", "ember", "frost"), problem="",
                 misses=()):
        self.names_in = list(names)
        self.problem = problem
        self.misses = set(misses)
        self.calls = []

    def build(self):
        return editor.Library(listing=lambda: list(self.names_in),
                              loader=self.load, creator=self.create)

    def load(self, name):
        self.calls.append(("load", name))
        if name in self.misses:
            return None
        return theme_slots(sum(ord(ch) for ch in name)), f"/themes/{name}.toml"

    def create(self, name, slots, force=False):
        self.calls.append(("create", name, force, dict(slots)))
        if self.problem:
            return "", self.problem
        self.names_in.append(name)
        return f"/themes/{name}.toml", ""


class PickerCase(unittest.TestCase):
    """Shared fixture: a theme session with a library and a scripted prompt."""

    def picker_state(self, names=("ash", "ember", "frost"), theme="ember",
                     prompts=(), problem="", misses=(), writes=None):
        writes = [] if writes is None else writes
        self.labels = []
        self.library = FakeLibrary(names, problem=problem, misses=misses)
        answers = list(prompts)
        st = editor.EditorState(dict(FULL_SLOTS), None,
                                theme=theme, fmt="ghostty",
                                library=self.library.build(),
                                path="/themes/ember.toml")

        def prompt_name(label):
            self.labels.append(label)
            return answers.pop(0) if answers else None

        def save(values):
            writes.append((st.theme, st.path, dict(values)))
            return f"saved {st.theme} → ghostty"

        st.prompt_name = prompt_name
        st.write = save
        self.writes = writes
        return st


class PickerKeys(PickerCase):
    """`t` and what the overlay does with keys (§13.7)."""

    def test_t_opens_the_picker_on_the_session_theme(self):
        st = self.picker_state()
        editor.apply_key("t", st)
        self.assertEqual(st.overlay, ["ash", "ember", "frost"])
        self.assertEqual(st.overlay_index, 1)      # ember, what we are editing
        self.assertEqual(st.status, "")
        self.assertEqual(st.picker_frame(),
                         (["ash", "ember", "frost"], 1, "ember"))

    def test_a_direct_session_opens_on_the_first_row(self):
        st = self.picker_state(theme=None)
        editor.apply_key("t", st)
        self.assertEqual(st.overlay_index, 0)
        self.assertEqual(st.picker_frame()[2], "")   # nothing to mark yet

    def test_arrows_move_the_selection_and_stop_at_the_edges(self):
        st = self.picker_state()
        editor.apply_key("t", st)
        editor.apply_key("up", st)
        self.assertEqual(st.overlay_index, 0)
        editor.apply_key("up", st)
        self.assertEqual(st.overlay_index, 0)
        editor.apply_key("down", st)
        editor.apply_key("down", st)
        editor.apply_key("down", st)
        self.assertEqual(st.overlay_index, 2)
        self.assertEqual(st.sel, 0)                # slot selection is untouched

    def test_enter_opens_the_theme_and_resets_the_session(self):
        st = self.picker_state()
        editor.apply_key("down", st)               # a selection to lose
        editor.apply_key("w", st)                  # and an edit to lose
        editor.apply_key("r", st)                  # clean again
        editor.apply_key("t", st)
        editor.apply_key("down", st)
        st.armed = True        # a pending discard must not follow the switch
        editor.apply_key("\r", st)
        self.assertEqual(st.theme, "frost")
        self.assertEqual(st.path, "/themes/frost.toml")
        self.assertEqual(st.slots, theme_slots(sum(ord(c) for c in "frost")))
        self.assertEqual(st.saved, st.slots)       # a fresh theme is clean
        self.assertEqual(st.undo, [])
        self.assertEqual(st.sel, 0)
        self.assertFalse(st.armed)                 # P2 review: no inherited arm
        self.assertIsNone(st.overlay)
        self.assertEqual(st.status, "opened frost")
        self.assertEqual(self.library.calls,
                         [("load", "frost")])
        self.assertEqual(self.writes, [])          # opening writes nothing

    def test_a_dirty_switch_is_blocked_with_the_exact_status(self):
        st = self.picker_state()
        editor.apply_key("w", st)                  # dirty
        dirty = dict(st.slots)
        editor.apply_key("t", st)
        editor.apply_key("down", st)
        editor.apply_key("\r", st)
        self.assertEqual(st.status, "save (Ctrl+S) or revert (r) first")
        self.assertEqual(st.theme, "ember")        # decision 12
        self.assertEqual(st.slots, dirty)
        self.assertIsNotNone(st.overlay)            # the picker stays up
        self.assertEqual(self.library.calls, [])   # nothing was even loaded
        self.assertEqual(self.writes, [])

    def test_esc_and_t_put_the_editor_back_without_quitting(self):
        for closer in ("esc", "t", "Q", "\x03"):
            with self.subTest(key=closer):
                st = self.picker_state()
                editor.apply_key("w", st)          # dirty: Esc must not quit
                editor.apply_key("t", st)
                editor.apply_key(closer, st)
                self.assertIsNone(st.overlay)
                self.assertFalse(st.quit)
                self.assertFalse(st.armed)
                self.assertEqual(st.status, "")
                self.assertEqual(st.theme, "ember")

    def test_the_picker_swallows_the_rest_of_the_key_surface(self):
        st = self.picker_state()
        before = dict(st.slots)
        editor.apply_key("t", st)
        for key in ("w", "a", "x", "u", "r", "f", SAVE, "i", "N"):
            editor.apply_key(key, st)
        self.assertEqual(st.slots, before)
        self.assertEqual(st.mult, 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(st.status, "")            # the picker answered, not `i`
        self.assertFalse(st.quit)
        self.assertEqual(st.overlay_index, 1)      # only arrows moved it

    def test_a_theme_that_will_not_open_keeps_the_buffer(self):
        st = self.picker_state(misses=("frost",))
        editor.apply_key("t", st)
        editor.apply_key("down", st)
        editor.apply_key("\r", st)
        self.assertEqual(st.status, "frost could not be opened")
        self.assertEqual(st.theme, "ember")
        self.assertEqual(st.slots, FULL_SLOTS)
        self.assertIsNotNone(st.overlay)

    def test_an_empty_library_says_so_and_opens_nothing(self):
        st = self.picker_state(names=())
        editor.apply_key("t", st)
        self.assertIsNone(st.overlay)
        self.assertIn("no themes yet", st.status)

    def test_without_a_library_the_picker_is_a_no_op(self):
        st = editor.EditorState(dict(FULL_SLOTS), lambda values: None)
        editor.apply_key("t", st)
        self.assertIsNone(st.overlay)
        self.assertEqual(st.status, "no theme library in this session")
        editor.apply_key("N", st)
        self.assertEqual(st.status, "no theme library in this session")
        self.assertIsNone(st.theme)


class PickerCreates(PickerCase):
    """`n` in the picker and `N` in the editor (§13.7)."""

    def test_n_creates_a_theme_from_the_buffer_and_stays_up(self):
        st = self.picker_state(prompts=["dusk"])
        editor.apply_key("w", st)                  # dirty: creation is fine
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        name, force, values = self.library.calls[0][1:]
        self.assertEqual((name, force), ("dusk", False))
        self.assertEqual(values, st.slots)         # the buffer, unsaved and all
        self.assertEqual(st.theme, "dusk")         # the session follows it
        self.assertEqual(st.path, "/themes/dusk.toml")
        self.assertEqual(st.overlay[-1], "dusk")   # the new row is selected
        self.assertEqual(st.overlay_index, len(st.overlay) - 1)
        self.assertEqual(st.status, "created dusk - current now")
        self.assertFalse(st.dirty())
        self.assertEqual(self.labels, ["  new theme name: "])

    def test_n_keeps_the_overlay_usable(self):
        st = self.picker_state(prompts=["dusk"])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        editor.apply_key("esc", st)
        self.assertIsNone(st.overlay)
        self.assertFalse(st.quit)

    def test_a_taken_name_is_confirmed_in_words_not_a_modal(self):
        st = self.picker_state(prompts=["ember", "y"])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(self.library.calls,
                         [("create", "ember", True, dict(st.slots))])
        self.assertIn("ember exists", self.labels[1])
        self.assertIn("y overwrites", self.labels[1])

    def test_another_name_at_the_prompt_replaces_the_answer(self):
        st = self.picker_state(prompts=["ember", "dusk"])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(self.library.calls[0][1:3], ("dusk", False))
        self.assertEqual(st.theme, "dusk")

    def test_a_cancelled_confirm_leaves_the_taken_theme_alone(self):
        for answer in ("", None):
            with self.subTest(answer=answer):
                st = self.picker_state(prompts=["ember", answer])
                editor.apply_key("t", st)
                editor.apply_key("n", st)
                self.assertEqual(self.library.calls, [])
                self.assertEqual(st.status, "cancelled - ember is untouched")
                self.assertEqual(st.theme, "ember")

    def test_a_cancelled_prompt_creates_nothing(self):
        st = self.picker_state(prompts=[""])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(self.library.calls, [])
        self.assertEqual(st.status, "cancelled - no theme created")

    def test_a_retype_that_is_also_taken_creates_nothing(self):
        st = self.picker_state(prompts=["ember", "frost"])
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(self.library.calls, [])
        self.assertIn("frost exists too", st.status)

    def test_a_refusal_comes_back_as_a_status_not_an_exception(self):
        problem = "invalid theme name (letters, digits, - and _, 64 max)"
        st = self.picker_state(prompts=["not a name"], problem=problem)
        editor.apply_key("t", st)
        editor.apply_key("n", st)
        self.assertEqual(st.status, problem)
        self.assertEqual(st.theme, "ember")
        self.assertEqual(self.writes, [])

    def test_capital_n_saves_the_buffer_as_a_new_theme(self):
        st = self.picker_state(prompts=["dusk"])
        editor.apply_key("w", st)
        editor.apply_key("N", st)
        name, force, values = self.library.calls[0][1:]
        self.assertEqual((name, force), ("dusk", False))
        self.assertEqual(values, st.slots)
        # ... and then through the ordinary save pipeline, push included
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0][:2], ("dusk", "/themes/dusk.toml"))
        self.assertEqual(self.writes[0][2], st.slots)
        self.assertEqual(st.status, "saved dusk → ghostty")
        self.assertTrue(st.written)
        self.assertFalse(st.dirty())
        self.assertEqual(st.undo, [])

    def test_capital_n_without_a_name_writes_nothing(self):
        st = self.picker_state(prompts=[None])
        editor.apply_key("w", st)
        editor.apply_key("N", st)          # prompt cancelled: no name
        self.assertEqual(self.writes, [])
        self.assertFalse(st.written)
        self.assertTrue(st.dirty())         # the buffer is untouched
        self.assertEqual(st.status, "cancelled - no theme created")
        self.assertEqual(st.theme, "ember")
        self.assertEqual(self.library.calls, [])

    def test_capital_n_migrates_a_direct_session_off_the_config(self):
        # §13.4 — `N` is the way out of a legacy direct-mode session
        st = self.picker_state(theme=None, prompts=["dusk"])
        st.path = "/home/you/.config/kitty/kitty.conf"
        st.backup_path = "/home/you/.config/kitty/kitty.conf"
        editor.apply_key("w", st)
        editor.apply_key("N", st)
        self.assertEqual(st.theme, "dusk")
        self.assertEqual(st.path, "/themes/dusk.toml")
        self.assertIsNone(st.backup_path)         # huebox owns it now: no .bak
        self.assertEqual(self.writes[0][:2], ("dusk", "/themes/dusk.toml"))
        self.assertEqual(editor.head_label(st), "dusk ghostty")  # saved


class StatusBar(unittest.TestCase):
    """`<theme> ● <fmt>` and `direct:<path>` (§13.7)."""

    def state(self, theme="ember", fmt="ghostty", path="/themes/ember.toml"):
        return editor.EditorState(dict(FULL_SLOTS),
                                  lambda values: "saved",
                                  theme=theme, fmt=fmt, path=path)

    def test_a_clean_theme_session_names_the_theme_and_the_target(self):
        self.assertEqual(editor.head_label(self.state()), "ember ghostty")

    def test_a_dirty_buffer_puts_a_dot_between_them(self):
        st = self.state()
        editor.apply_key("w", st)
        self.assertTrue(st.dirty())
        self.assertEqual(editor.head_label(st), "ember ● ghostty")
        editor.apply_key(SAVE, st)                # saved: clean again
        self.assertEqual(editor.head_label(st), "ember ghostty")

    def test_with_no_named_target_only_the_theme_is_shown(self):
        self.assertEqual(editor.head_label(self.state(fmt="")), "ember")
        st = self.state(fmt="")
        editor.apply_key("w", st)
        self.assertEqual(editor.head_label(st), "ember ●")

    def test_a_direct_session_names_the_config(self):
        st = self.state(theme=None, path="/tmp/kitty.conf")
        self.assertEqual(editor.head_label(st), "direct:/tmp/kitty.conf")
        self.assertEqual(editor.session_path(st), "")      # not printed twice
        self.assertEqual(editor.session_path(self.state()),
                         "/themes/ember.toml")

    def test_the_head_is_width_aware(self):
        # the dim path is dropped when it does not fit beside the subject
        out = io.StringIO()
        with mock.patch.object(editor, "term_size", return_value=(40, 24)), \
                mock.patch.object(sys, "stdout", out):
            editor.draw_editor("", "/themes/a-very-long-theme-name.toml",
                               FULL_SLOTS, 0, [], "", 1,
                               head=editor.head_label(
                                   self.state(path="/themes/a-very-long-"
                                                  "theme-name.toml")))
        self.assertNotIn("a-very-long-theme-name.toml",
                         ANSI.sub("", out.getvalue()))


class OverlayFrame(unittest.TestCase):
    """The picker frame inside the width and height budget (§13.7, §15)."""

    MANY = [f"theme-{index:02d}" for index in range(34)]

    def overlay_frame(self, cols, rows, names=("ash", "ember", "frost"),
                      index=0, current="ember", status=""):
        out = io.StringIO()
        with mock.patch.object(editor, "term_size", return_value=(cols, rows)),\
                mock.patch.object(sys, "stdout", out):
            editor.draw_editor("ghostty", "", dict(FULL_SLOTS), 0, [], status,
                               1, overlay=(list(names), index, current))
        return out.getvalue()

    def test_the_frame_names_the_themes_and_marks_the_current_one(self):
        body = ANSI.sub("", self.overlay_frame(80, 24))
        self.assertIn("themes", body)
        self.assertIn("* ember", body)             # the library's current
        self.assertIn("ash", body)
        self.assertIn("> ash", body)               # and the selection
        self.assertIn("Enter open", body)

    def test_every_line_holds_the_width_and_the_frame_the_height(self):
        for cols, rows in ((100, 30), (80, 24), (60, 16), (40, 12)):
            with self.subTest(size=(cols, rows)):
                body = lines(self.overlay_frame(cols, rows, self.MANY, 17))
                self.assertLessEqual(len(body), rows)
                for line in body:
                    self.assertLessEqual(width(line), cols)

    def test_a_long_library_scrolls_with_the_selection_in_view(self):
        body = ANSI.sub("", self.overlay_frame(40, 12, self.MANY, 17,
                                               status="opened theme-17"))
        self.assertIn("> theme-17", body)          # the selection is on screen
        self.assertIn("of 34", body)               # and the window is counted
        self.assertLessEqual(body.count("theme-"), 12)
        # the last row is reachable too: the window follows, it never runs off
        last = ANSI.sub("", self.overlay_frame(40, 12, self.MANY, 33))
        self.assertIn("34 of 34", last)

    def test_the_hint_footer_folds_instead_of_overflowing(self):
        for cols in (40, 60, 80):
            with self.subTest(cols=cols):
                body = lines(self.overlay_frame(cols, 12))
                hints = [ANSI.sub("", line) for line in body
                         if "move" in ANSI.sub("", line)
                         or "open" in ANSI.sub("", line)
                         or "new" in ANSI.sub("", line)
                         or "back" in ANSI.sub("", line)]
                self.assertTrue(hints)
                for line in hints:
                    self.assertLessEqual(width(line), cols)
        # at the minimum width the footer cannot fit on one row, so it wraps
        # (§13.7 - pack(), not a clipped single line)
        narrow = lines(self.overlay_frame(40, 24))
        self.assertGreaterEqual(
            len([line for line in narrow if "move" in ANSI.sub("", line)
                 or "back" in ANSI.sub("", line)]), 2)

    def test_a_64_character_name_is_clipped_not_wrapped(self):
        long = "x" * 64
        body = lines(self.overlay_frame(40, 12, [long], 0))
        self.assertLessEqual(len(body), 12)
        for line in body:
            self.assertLessEqual(width(line), 40)
        self.assertIn("x" * 30, ANSI.sub("", "\r\n".join(body)))

    def test_an_empty_library_says_how_to_start_one(self):
        body = ANSI.sub("", self.overlay_frame(80, 24, []))
        self.assertIn("no themes yet", body)
        self.assertIn("N makes one from this buffer", body)

    def test_the_status_line_is_the_last_row(self):
        body = lines(self.overlay_frame(80, 24, status="save (Ctrl+S) or "
                                                     "revert (r) first"))
        self.assertIn("save (Ctrl+S) or revert (r) first", ANSI.sub("", body[-1]))

    def test_the_frame_is_byte_identical_for_identical_input(self):
        self.assertEqual(self.overlay_frame(60, 16, self.MANY, 4, "theme-04"),
                         self.overlay_frame(60, 16, self.MANY, 4, "theme-04"))

    def test_below_the_minimum_the_hint_replaces_the_picker(self):
        for cols, rows in ((editor.MIN_COLS - 1, 24),
                           (80, editor.MIN_ROWS - 1)):
            with self.subTest(size=(cols, rows)):
                out = self.overlay_frame(cols, rows, self.MANY)
                self.assertIn(HINT, out)
                self.assertNotIn("theme-", out)
                self.assertNotIn("Enter open", out)


class RawMode(unittest.TestCase):
    """§4.3 — one enter/exit pair per raw session, prompts included.

    The paths that leave raw mode are: the quit (clean or armed), each
    prompt (hex entry and the picker's name prompts), and any exception out
    of the loop. Every one of them has to come back to a sane terminal.
    """

    def run_session(self, keys, answers=(), input_error=None, edit_kwargs=None,
                    keys_side_effect=None, exit_error=None):
        events = []
        entered = []

        def enter_raw():
            state = (10 + len(entered), f"termios-{len(entered)}")
            entered.append(state)
            events.append(("enter", state))
            return state

        def exit_raw(fd, saved):
            events.append(("exit", (fd, saved)))
            if exit_error is not None:
                raise exit_error

        def fake_input(label):
            events.append(("prompt", label))
            if input_error is not None:
                raise input_error
            return answers.pop(0) if answers else ""

        stream = iter(keys)
        with mock.patch.object(editor, "enter_raw", enter_raw), \
                mock.patch.object(editor, "exit_raw", exit_raw), \
                mock.patch.object(editor, "draw_editor"), \
                mock.patch.object(editor, "read_key",
                                  side_effect=keys_side_effect
                                  or (lambda fd: next(stream))), \
                mock.patch("builtins.input", fake_input), \
                mock.patch.object(sys, "stdout", io.StringIO()), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            editor.edit("ghostty", "/tmp/huebox.conf", dict(FULL_SLOTS),
                        lambda name, path, values: "saved",
                        **(edit_kwargs or {}))
        return events

    def pairs(self, events):
        """Every exit paired with the enter whose state it restores."""
        out, live = [], None
        for kind, payload in events:
            if kind == "enter":
                live = payload
            elif kind == "exit":
                out.append((live, payload))
                live = None
        return out

    def test_a_session_without_prompts_enters_once_and_exits_once(self):
        events = self.run_session(["f", "esc"])
        self.assertEqual(events, [("enter", (10, "termios-0")),
                                  ("exit", (10, "termios-0"))])

    def test_a_dirty_quit_exits_the_same_way(self):
        events = self.run_session(["w", "esc", "esc"])
        self.assertEqual(len(self.pairs(events)), 1)
        self.assertEqual(events[-1], ("exit", (10, "termios-0")))

    def test_each_prompt_closes_and_reopens_the_pair(self):
        library = FakeLibrary().build()
        events = self.run_session(
            ["i", "N", "esc"], answers=["#abcdef", "dusk"],
            edit_kwargs={"theme": "ember", "backup": False,
                         "library": library})
        self.assertEqual([kind for kind, _ in events],
                         ["enter", "exit", "prompt", "enter", "exit", "prompt",
                          "enter", "exit"])
        # every exit restores exactly the state its own enter saved
        self.assertEqual(self.pairs(events),
                         [((10, "termios-0"), (10, "termios-0")),
                          ((11, "termios-1"), (11, "termios-1")),
                          ((12, "termios-2"), (12, "termios-2"))])

    def test_the_closing_exit_uses_the_last_enter_state(self):
        # the loop's finally restores the termios state of the *current*
        # raw session, not the one from before a prompt (§4.3)
        library = FakeLibrary().build()
        events = self.run_session(
            ["i", SAVE, "esc"], answers=["#abcdef"],
            edit_kwargs={"theme": "ember", "backup": False,
                         "library": library})
        self.assertEqual(events[-1], ("exit", (11, "termios-1")))

    def test_a_cancelled_prompt_still_closes_the_pair(self):
        for problem in (EOFError(""), KeyboardInterrupt()):
            with self.subTest(problem=type(problem).__name__):
                library = FakeLibrary().build()
                events = self.run_session(
                    ["i", "N", "esc"], input_error=problem,
                    edit_kwargs={"theme": "ember", "backup": False,
                                 "library": library})
                self.assertEqual([kind for kind, _ in events],
                                 ["enter", "exit", "prompt", "enter", "exit",
                                  "prompt", "enter", "exit"])
                self.assertEqual(self.pairs(events)[-1],
                                 ((12, "termios-2"), (12, "termios-2")))

    def test_an_exception_out_of_the_loop_still_exits_raw(self):
        before = signal.getsignal(signal.SIGWINCH)
        with self.assertRaises(RuntimeError):
            self.run_session(["x"], keys_side_effect=["x", RuntimeError("boom")])
        self.assertEqual(signal.getsignal(signal.SIGWINCH), before)

    def test_the_winch_handler_survives_a_failing_termios_restore(self):
        # nothing may leave the user with a raw shell *or* a stale handler
        before = signal.getsignal(signal.SIGWINCH)
        with self.assertRaises(OSError):
            self.run_session(["esc"], exit_error=OSError("tty"))
        self.assertEqual(signal.getsignal(signal.SIGWINCH), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
