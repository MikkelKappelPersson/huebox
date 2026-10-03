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
| `huebox/tui.py` | `term_size`, raw mode, `read_key`, SIGWINCH, `MIN_COLS`/`MIN_ROWS` | §15 |
| `huebox/editor.py` | draw loop, keys, picker + save-as-new, staged buffer + save; `REQUIRES`, the extras the editor needs | §4.3, §13.7, §14 |
| `huebox/app.py` | the Textual shell: the frame as one widget over `render`'s rows, keys, resize | migration §5.5 |
| `huebox/cli.py` | argparse, dispatch, theme commands, exit codes; `main()` | §4, §13.5 |

Dependency rule, no exceptions: `color` imports nothing intra-package;
`formats` and `tui` import `color` only; `detect` imports `formats`;
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
wrong, and the equivalence harness could then only say the two copies agreed —
not that either matched what huebox used to do. Take the buffer as a file
(`HUEBOX_SLOTS`): product code is handed its slots, and the fixtures stay in
`tests/`.

**Injected seams keep those edges clean.** `editor.py` reaches the outside
world through four callables `cli.py` builds: the save callback
(`write(theme, path, slots)` — handed the subject every time, because a
picker switch retargets it mid-session), `prompt_hex` / `prompt_name` (one
raw-mode prompt pattern, §4.3), and the `Library` object (list / load /
create) that backs the theme picker (§13.7). Never import `themes` or
`detect` into `editor` to save a parameter.

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
- **The colour promise is a test, not a review habit.** Moving anything onto
  Textual's compositor is gated on I1: the frame after the change must equal
  the committed golden cell for cell, parsed through `pyte` from a real pty
  (`tests/candidate.py`, `docs/001-spec/textual-migration.md` §4). Two
  consequences: pin the whole environment when launching the app (Textual reads
  eight variables at import time, §6.4), and never let a golden regenerate to
  make a failure go away — the golden diff *is* the review artefact.
- **Extras are not dependencies.** `pyproject` keeps one runtime dependency
  (Pygments); `textual` is the `editor` group and `pyte` the `test` group, so
  `show` / `list` / `use` / `new` / `import` install without either. The
  equivalence tests skip without them rather than comparing the reference with
  itself. `editor.REQUIRES` is what `cli` reads to say so — add a name there
  only in the same commit that makes `huebox edit` use the module needing it.
- Keep it single-purpose: colour slots in, colour slots out. Not a config
  editor, not a theme store (§3).
