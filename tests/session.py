"""Drive one editor session without a terminal (the deletion of `editor.edit`).

The editor is the Textual shell now, and a compositor will not let a test feed
it keys or capture its frames. `cli._run_editor` therefore names its `driver`,
and this is the test's choice of one: the loop `editor.edit` used to be, with
the raw-mode half removed because a key *list* needs no terminal to read from.

What this covers and what it does not is worth being exact about. It exercises
`EditorState`, `apply_key` and `draw_editor` — which is where all of huebox's
editing behaviour lives. It does not
exercise the compositor, and it is not meant to: `tests/candidate.py` launches
the real app in a pty for that. Together they cover "the session is wired" and
"the shell is faithful", which neither covers alone.

The signature matches the `editor.edit` that this replaces, so the call sites
read the same as they did.
"""

from __future__ import annotations

import sys

from huebox import editor, tui


def drive(keys, fmt, path, slots, write, backup=False, theme=None,
          report=None, library=None, notes=None, size=(100, 24), prompt=None,
          draw=None):
    """Run one session over `keys`; return its `EditorState`.

    `keys` is iterated in order. `"resize"` redraws without consuming a key or
    touching the buffer, which is what the old loop did when SIGWINCH fired and
    what Textual's own `on_resize` now does.

    `draw` replaces `editor.draw_editor` for the tests that capture frames;
    `prompt` answers the two prompt seams.
    """
    stream = iter(keys)

    def next_key(_fd=None):
        key = next(stream)
        # A session ends when it is told to; running out of keys is how a test
        # says "no more", rather than an IndexError from inside apply_key.
        if key is None:
            raise StopIteration
        return key

    prompt = prompt or (lambda label: input(label).strip())
    original_size = tui.term_size
    original_draw = editor.draw_editor
    # The hook *replaces* the draw for the duration: a capture wants to see the
    # call, so calling the saved original would leave `drawn` empty. Restore it
    # either way, so one test's hook cannot leak into the next.
    paint = draw or original_draw
    tui.term_size = lambda default=(80, 24): size
    state = None
    try:
        state = editor.EditorState(slots, None, prompt,
                                   path if backup else None, theme=theme,
                                   fmt=fmt, library=library, path=path)
        state.prompt_name = prompt
        state.write = lambda values: write(state.theme, state.path, values)
        if state.overlay is None:
            # An empty library opens on the first-run choice, like the
            # shell does — a headless session with nothing to save to
            # chooses first and edits after.
            editor.enter_setup(state)
        while True:
            # §15.2 — one geometry per frame, read by the frame and by the keys:
            # what is drawn and what the arrows step through cannot disagree
            state.grid = editor.grid_geometry(size[0])
            # No stdout redirection here: the frame goes wherever `sys.stdout`
            # points, which is how the suites capture it — `editor.edit` wrote to
            # the real stdout too, and swallowing it into a throwaway would make
            # every status-line assertion see nothing.
            if state.setup is not None:
                # The first-run choice is its own frame, like the picker:
                # `setup_lines`, then `backdrop` — naming mode included.
                for line in editor.setup_lines(state.setup, size[0], size[1],
                                               state.status, state.slots,
                                               name=state.setup_name):
                    sys.stdout.write(editor.backdrop(line, state.slots, size[0])
                                     + "\r\n")
            elif state.picker_frame() is not None:
                # §13.7 — the picker rows are `theme_lines`, then `backdrop`
                # (headless draws full-frame, the app's dialog at its own
                # width): this harness paints whichever frame is up, exactly
                # as the behaviour owns it.
                overlay = state.picker_frame()
                for line in editor.theme_lines(*overlay, size[0], size[1],
                                               state.status, state.slots):
                    sys.stdout.write(editor.backdrop(line, state.slots, size[0])
                                     + "\r\n")
            else:
                paint(fmt, editor.session_path(state), state.slots,
                      state.sel, state.undo, state.status, state.mult,
                      head=editor.head_label(state),
                      grid=state.grid)
            try:
                key = next_key()
            except StopIteration:
                break
            if key == "resize":
                continue
            editor.apply_key(key, state)
            if state.quit:
                break
    finally:
        tui.term_size = original_size
        editor.draw_editor = original_draw
    editor.report_session(state, report, notes)
    return state


def driver_factory(keys, size=(100, 24), prompt=None, draw=None):
    """`drive`, shaped like `app.run` — the argument `cli._run_editor` takes.

    So a suite that wants to exercise `cli`'s *wiring* can hand it a session it
    can feed, rather than a compositor it cannot:

        cli._run_editor(target, spec, driver=session.driver_factory(keys))
    """
    def run(fmt, path, slots, write, backup_path=None, theme=None,
            library=None, report=None, notes=None,
            import_library=None, import_writer=None):
        # Shaped like `app.run`: the import seams arrive here and are
        # ignored — a headless double has no popup to open, so there is
        # nothing to list and nothing to write.
        return drive(keys, fmt, path, slots, write,
                     backup=backup_path is not None, theme=theme,
                     library=library, report=report, notes=notes,
                     size=size, prompt=prompt, draw=draw)
    return run
