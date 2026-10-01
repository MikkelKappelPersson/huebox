# AGENTS.md — huebox

Terminal theme editor with live preview. Stdlib only, Python ≥ 3.9, zero
required third-party dependencies (Pygments optional, code sample only).

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
| `huebox/detect.py` | probes, candidate paths, Ghostty includes / `theme =`, `resolve()` | §7 |
| `huebox/themes.py` | home, `state.toml`, theme files, `new` / `import` / `use` / `list` | §13 |
| `huebox/render.py` | `clip` / `pack`, samples, static preview, examples strip | §8, §14 |
| `huebox/tui.py` | `term_size`, raw mode, `read_key`, SIGWINCH, `MIN_COLS`/`MIN_ROWS` | §15 |
| `huebox/editor.py` | draw loop, keys, overlay, staged buffer + save | §4.3, §14 |
| `huebox/cli.py` | argparse, dispatch, exit codes; `main()` | §4 |

Dependency rule, no exceptions: `color` imports nothing intra-package;
`formats` and `tui` import `color` only; `detect` imports `formats`;
`themes` imports `color` + `formats`; `render` imports `color`; `editor`
imports `render` + `tui` + `color`; `cli` imports everything. No cycles.
Every module header cites its spec section.

## Guidelines

- **Line-level writes only.** Never re-serialise a terminal config; only
  colour tokens are replaced. A no-op write is byte-identical (tested).
- **Theme files are ours; configs are theirs.** `themes/` may be rewritten
  freely; anything under Ghostty/kitty/Alacritty config follows §6.2.
- **Buffer renders, save writes.** The editor draws from the in-memory buffer
  every frame; disk changes happen on Ctrl+S only (§14).
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
  a new format needs round-trip plus byte-identical no-op cases.
- Keep it single-purpose: colour slots in, colour slots out. Not a config
  editor, not a theme store (§3).
