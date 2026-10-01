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
| `huebox/themes.py` | home, `state.toml`, theme files, canonical writer + subset reader, `push` to terminals, Ghostty native export, `RAMP` | §13, §13.6 |
| `huebox/render.py` | `clip` / `pack`, samples, static preview, examples strip | §8, §14 |
| `huebox/tui.py` | `term_size`, raw mode, `read_key`, SIGWINCH, `MIN_COLS`/`MIN_ROWS` | §15 |
| `huebox/editor.py` | draw loop, keys, picker + save-as-new, staged buffer + save | §4.3, §13.7, §14 |
| `huebox/cli.py` | argparse, dispatch, theme commands, exit codes; `main()` | §4, §13.5 |

Dependency rule, no exceptions: `color` imports nothing intra-package;
`formats` and `tui` import `color` only; `detect` imports `formats`;
`themes` imports `color` + `formats` + `detect` (push resolves its target
through the same `resolve()` the CLI does, and the native export asks
`detect` for the config holding the `theme =` line); `render` imports
`color`; `editor` imports `render` + `tui` + `color`; `cli` imports
everything. No cycles. Every module header cites its spec section.

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
  never invents a key, and never re-serialises. A `--ghostty-native` export
  is the one deliberate exception, and it is a whole file *we* name inside
  Ghostty's theme dir, opted into; the user's config still gets exactly one
  line, the `theme =` pointer.
- **Truth first, never rollback.** A save writes `themes.save()` and only
  then pushes; a push that fails is a report plus exit 1 and leaves the
  theme file exactly as written (spec decision 7).
- **Buffer renders, save writes.** The editor draws from the in-memory buffer
  every frame; disk changes happen on Ctrl+S only (§14).
- **The editor asks, it never reaches.** Nothing in the draw loop may write
  to stdout outside a frame, prompt inside raw mode, or import a module the
  dependency rule forbids. Anything the picker cannot say in one status line
  is collected by `cli` and printed after the session, on stderr (§13.7).
- **3.9-compatible code.** No `match`, no `tomllib` (3.11+ — the TOML subset
  parser stays hand-rolled), no runtime `X | Y` (keep
  `from __future__ import annotations` in every file).
- **Errors to stderr, prefixed `huebox: `, exit 1.** No tracebacks for user
  errors: missing config, bad theme name, no colours found.
- **Layout goes through `clip` / `pack`.** No widget measures width itself;
  layout derives from `term_size()` every frame (§15). Below
  `MIN_COLS`×`MIN_ROWS` (in `tui.py`) the editor draws the too-small hint and
  nothing else.
- **Tests stay green:** `python3 -m unittest discover -s tests`. Mirror the
  module under test (`tests/test_<module>.py`); new behaviour needs a case,
  a new format needs round-trip plus byte-identical no-op cases. Run it with
  `-W always` before calling a phase done — the warning count is part of the
  contract.
- Keep it single-purpose: colour slots in, colour slots out. Not a config
  editor, not a theme store (§3).
