# AGENTS.md — huebox

Terminal theme editor with live preview. Python ≥ 3.9; Pygments is the one
third-party dependency (the editor's live code sample), everything else is
stdlib.

**Spec-first:** `docs/001-spec/spec.md` is the source of truth for *what*
huebox does. Change the spec alongside the code — a behaviour change with no
spec update is incomplete. This file covers *how* the code is organised.

## Architecture

Data flows one way:

```
terminal config → canonical slots → edit buffer → truth file → push to terminal
```

| Module | Owns | Spec |
| --- | --- | --- |
| `huebox/color.py` | 22-slot model, hex/rgb/hsv maths, luminance | §5 |
| `huebox/formats/` | `base` (rule machinery, flat read/write) + `ghostty`, `kitty`, `alacritty` (TOML); registry in `__init__` | §6 |
| `huebox/detect.py` | probes and env overrides, candidate paths, Ghostty `config-file` includes / `theme =` reads and the pointer writer, `config_holds_colours`, `resolve()` | §7 |
| `huebox/themes.py` | home, `state.toml`, theme files, canonical writer + subset reader, `push` to terminals, the post-push reload, Ghostty native export, `RAMP` | §13, §13.6 |
| `huebox/render.py` | `clip` / `pack` / `visible`, frame typography (`chrome` / `title` / `wordmark`), samples, static preview, examples strip, live diff | §8, §8.1, §14 |
| `huebox/tui.py` | `term_size`, and nothing else: Textual owns input, resize and raw mode | §15 |
| `huebox/editor.py` | the session: `EditorState`, `apply_key`, the picker, staged save, `report_session` | §4.3, §13.7, §14 |
| `huebox/app.py` | the Textual shell: the frame as one widget per block over `render`'s rows, keys, focus, resize, click and wheel | migration §5.5 |
| `huebox/cli.py` | argparse, dispatch, theme commands, exit codes; `main()` | §4, §13.5 |

Dependency rule, no exceptions: `color` imports nothing intra-package;
`formats` imports `color` only, and `tui` imports nothing intra-package; `detect` imports `formats`;
`themes` imports `color` + `formats` + `detect` (push resolves its target
through the same `resolve()` the CLI does, and the native export asks
`detect` for the config holding the `theme =` line); `render` imports
`color`; `editor` imports `render` + `tui` + `color`; `app` imports
`render` + `tui` + `color` + `editor` (it drives `editor`'s grid geometry and
step arithmetic, and must not re-implement either); `cli` imports
everything. No cycles. Every module header cites its spec section.

**`app.py` reuses `draw_editor`'s rows, it does not re-render them.** It
captures what the writer produced and hands it to Textual as a `Strip`. A
second copy of the frame's construction would be a second chance to get it
wrong, and two copies agreeing would prove nothing. Take the buffer as a file
(`HUEBOX_SLOTS`): product code is handed its slots, and the fixtures stay in
`tests/`.

**Injected seams keep those edges clean.** `editor.py` reaches the outside
world through four callables `cli.py` builds: the save callback
(`write(theme, path, slots)` — handed the subject every time, because a
picker switch retargets it mid-session), `prompt_hex` / `prompt_name` (one
prompt pattern, §4.3, which the shell satisfies by handing the terminal back
with `App.suspend()` so the call stays synchronous), and the `Library` object
(list / load / create) that backs the theme picker (§13.7). Never import
`themes` or `detect` into `editor` to save a parameter.

**`cli` names its session, and imports the shell late.** `_run_editor` takes a
`driver` defaulting to `app.run`, which is what lets a test drive a session
without a compositor (`tests/session.py`) and keeps `_run_editor` about the
writer and the picker rather than about Textual. The `app` import is inside the
function: eager, every huebox invocation — `show`, `list`, `--dump` — paid
Textual's import and the suite got 4x slower on one module, which is how it was
found. Order in that function matters: the tty test, then the import, so a
piped session says what is actually wrong with it instead of opening an
editor it will never use.

## Guidelines

- **Line-level writes only.** Never re-serialise a terminal config; only
  colour tokens are replaced. A no-op write is byte-identical *and* never
  opens the file for writing — both are tested, bytes and mtime (§6.2 rules
  3 and 4). Compare first, write second.
- **Readers use `with open(...)`.** No bare `open()` anywhere, in `huebox/` or
  `tests/`: an unclosed handle is a ResourceWarning at GC, long after the test
  that made it, and the suite is expected to be clean under `-W always`.
- **Theme files are ours; configs are theirs.** `themes/` may be rewritten
  freely — canonical layout, written to a `.tmp` and renamed over the file,
  never partially written; anything under Ghostty/kitty/Alacritty config
  follows §6.2, which includes the push: it reuses `FORMATS[fmt]["write"]`,
  never invents a key, and never re-serialises. Two deliberate exceptions,
  both files huebox names after a theme and owns: the Ghostty export
  (`export_ghostty_native`), which a save uses by default whenever the
  config is organised by theme, and the one `theme =` pointer line
  `ensure_theme_pointer` moves. The rule under both is that a theme's
  colours never land in a file that belongs to another theme — so a save
  either writes a file named after the theme or edits a file no theme is
  named after, and `_foreign_theme` refuses the crossing.
- **Truth first, never rollback.** A save writes `themes.save()` and only
  then pushes; a push that fails is a report plus exit 1 and leaves the
  theme file exactly as written (spec decision 7).
- **Buffer renders, save writes.** The editor draws from the in-memory buffer
  every frame; disk changes happen on Ctrl+S only (§14).
- **The editor asks, it never reaches.** Nothing in the draw loop may write
  to stdout outside a frame, prompt inside raw mode, or import a module the
  dependency rule forbids. A save *is* the ask: the injected callback is
  where the terminal is written and asked to reload, all of it behind the
  editor's back and reported afterwards. Anything the picker cannot say in
  one status line is collected by `cli` and printed after the session, on
  stderr (§13.7).
- **3.9-compatible code.** No `match`, no `tomllib` (3.11+ — the TOML subset
  parser stays hand-rolled), no runtime `X | Y` (keep
  `from __future__ import annotations` in every file).
- **Errors to stderr, prefixed `huebox: `, exit 1.** No tracebacks for user
  errors: missing config, bad theme name, no colours found.
- **One handler per key.** A key the focused widget binds is the widget's
  business and the app's `on_key` steps over it. Textual does not promise a
  binding consumes a key before the app's own handler sees it, and when both
  acted on an arrow the selection moved two slots per press.
- **Focus is placed deliberately, after the tree exists.** `mount` is a
  request, so focusing inline queries an empty tree; and `self.size` during
  `on_resize` is still the *old* size, so a frame drawn there is laid out for
  the window the user just left. Both want `call_after_refresh`.
- **One size, one number.** The frame is laid out from the compositor's size,
  passed down as `draw_editor`'s `size=`. Asking the terminal as well gives two
  numbers for one quantity, and a pty capture cannot see the disagreement: it sets
  the pty size before launching, so they only diverge on a resize.
- **A widget that answers to the user says so.** `Frame.ALLOW_SELECT` is off
  and only the sample and the diff turn it on: Textual makes every widget
  selectable by default, and a swatch that began a text selection when clicked
  would swallow the click.
- **A click is a keypress.** `on_click` resolves to a slot and then goes
  through `apply_key`; it must never touch a colour or a frame. The clickable
  cells are recorded by the rows that draw them (`draw_editor` / `theme_lines`
  take `hits=`), never recomputed — a second copy of the layout is a second
  chance to aim a click at the wrong cell, silently — and `test_editor`
  cross-checks every hit against the painted frame.
- **Layout goes through `clip` / `pack`.** No widget measures width itself;
  layout derives from `term_size()` every frame (§15). Below
  `MIN_COLS`×`MIN_ROWS` (in `tui.py`) the editor draws the too-small hint and
  nothing else.
- **The frame's own text is chrome, not a terminal attribute.** Every string
  the frame says about itself — a key, a label, a header, a path — is painted
  from the buffer through `render.chrome()` / `title()` / `wordmark()` (§8.1),
  never with `DIM`, so the live-everything property (§14.1) covers the frame's
  wording too. Headers wear `foreground`; only the wordmark and the hint keys
  take a palette slot.
- **Tests stay green:** `python3 -m unittest discover -s tests`. Mirror the
  module under test (`tests/test_<module>.py`); new behaviour needs a case,
  a new format needs round-trip plus byte-identical no-op cases. Run it with
  `-W always` before calling a phase done — the warning count is part of the
  contract.
- **One optional group, not two.** `pyproject` keeps two runtime dependencies
  (Pygments, Textual); `pyte` is the `test` group. An install whose main
  command fails on open is not a smaller install (decision 32), so Textual
  stopped being the `editor` extra — and the missing-extra guard went with it,
  rather than guarding a configuration that can no longer be produced. The
  closure tests still skip without `pyte` rather than comparing the
  reference with itself. The `app` import stays late in `cli`, but that was
  always about cold-start time for the commands that never open the editor,
  never about installability.
- Keep it single-purpose: colour slots in, colour slots out. Not a config
  editor, not a theme store (§3).
