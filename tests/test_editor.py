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
from huebox.color import SLOTS  # noqa: E402

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

    def test_examples_and_sample_shrink_together(self):
        # short terminal: the strip takes the leftover rows, the sample
        # follows only when there is room left for both
        short = ANSI.sub("", "\r\n".join(lines(frame(60, 24))))
        self.assertIn("examples", short)
        self.assertNotIn("live code", short)
        tall = ANSI.sub("", "\r\n".join(lines(frame(60, 30))))
        self.assertIn("live code", tall)
        self.assertIn("examples", tall)


class EditLoop(unittest.TestCase):
    """The loop itself: draw / read / apply, and only save writes (§14.2)."""

    def run_session(self, keys, path=None, slots=None):
        """Drive `edit()` with a scripted key stream; return the writes."""
        writes, draws, handlers = [], [], []

        def record(target, values):
            writes.append((target, dict(values)))

        def note_handler(*args):
            draws.append({"slots": dict(args[2]), "status": args[5]})
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
        self.assertEqual(writes[0][0], path)
        # the frame drawn after "w" already carried the adjusted value,
        # before any save happened (palette-0 is the selected slot)
        self.assertNotEqual(draws[0]["slots"]["palette-0"],
                            draws[1]["slots"]["palette-0"])
        self.assertEqual(draws[1]["slots"]["palette-0"],
                         writes[0][1]["palette-0"])

    def test_a_dirty_quit_takes_two_escapes(self):
        writes, draws, _, _ = self.run_session(["w", "esc", "esc"])
        self.assertEqual(writes, [])        # discarded, not saved
        self.assertIn("unsaved changes", draws[-1]["status"])
        self.assertEqual(len(draws), 3)      # armed frame is drawn once


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
        self.assertEqual(asked, ["background"])
        self.assertEqual(st.slots["background"], "#abcdef")
        self.assertEqual(len(st.undo), 2)   # the nudge plus the typed hex
        self.assertEqual(st.status, "background = #abcdef")
        editor.apply_key("u", st)
        self.assertEqual(st.status, "undid background")
        self.assertEqual(writes, [])
        editor.apply_key("X", st)
        self.assertEqual(asked[-1], "background")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)