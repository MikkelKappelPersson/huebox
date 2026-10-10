# huebox Code Guidelines

Binding code, architecture, and design rules for huebox.
Referenced by [AGENTS.md](../../AGENTS.md), which mandates reading this document
before writing or modifying any code, or doing any architecture or design work.
Keep changes here in step with the code: a behaviour change with no guideline
update is incomplete.

## Core Design Principles

### 1. Modular Design
Isolates responsibilities so changes in one module don't cascade; enables independent testing.
- Each module owns one job (see Architecture table below)
- Minimal cross-module dependencies; the dependency rule under Architecture is absolute — no cycles
- `color` and `tui` import nothing intra-package; everything else imports only what the rule allows

### 2. Facade Pattern
Shields callers from subsystem complexity; lets internals evolve without breaking consumers.
- The `FORMATS` registry (`FORMATS[fmt]["read"]` / `["write"]`) is the facade for terminal formats — the push reuses it, never invents a key
- The `PROVIDERS` registry (`PROVIDERS[name]["list"]` / `["read"]`) is the facade for provider themes — the import popup lists and reads through it, never a directory walk of its own
- `themes.save` / `themes.push` is the facade for writing theme files and pushing to terminals; `themes.create` is the facade for new library files — the import confirm writes through it, never a file of its own
- `detect.resolve()` is the facade for finding the push target — the push resolves its target through the same `resolve()` the CLI does
- `app` / `cli` never reach into format internals or config parsing directly — use the facades

### 3. Dependency Injection
Decouples the editing session from the outside world; makes the session testable without a compositor.
- `editor` reaches outside only through injected callables that `cli` builds: the save callback (`write(theme, path, slots)`) and the preview callback (`apply(theme, path, slots)`) — each handed the subject every time, since a picker switch retargets it mid-session — `prompt_hex` / `prompt_name` (one prompt pattern), the `Library` object (`list` / `load` / `create`) behind the theme picker, and the import popup's `ImportLibrary` (`list` / `read` per provider) plus its `themes.create` writer
- NEVER import `themes` or `detect` into `editor` to save a parameter
- The import flow follows the same rule: `import_state` takes an injected `ImportLibrary`, `app` takes an injected writer — neither imports `themes` or `detect`
- `cli` is the composition root: `_run_editor` takes a `driver` defaulting to `app.run`, so tests drive a session without a compositor (`tests/session.py`)
- The `app` import stays late inside `_run_editor` (cold-start time for commands that never open the editor); the tty check runs before the import so a piped session reports the real problem

### 4. State/View Separation
Separates editing behaviour from the compositor; enables testing behaviour without Textual.
- `EditorState` + `apply_key` own ALL editing behaviour — selection, adjust, undo, revert, step size, picker, first-run setup choice, save, apply, two-armed Esc. `app` owns only keys-in, redraw, and the two prompts
- `app` reuses `draw_editor`'s rows (handed to Textual as `Strip`s) — it never re-renders the frame, re-implements grid geometry, or duplicates step arithmetic (`HUE_STEP` / `CHANNEL_STEP` in `color` is the single source)
- The editor draws from the in-memory buffer every frame; truth reaches disk on Ctrl+S only, a preview push on Ctrl+A only — and the preview never moves `saved`, so the buffer stays dirty and Esc keeps guarding it
- A click resolves to a slot and goes through `apply_key` — it never touches a colour or a frame directly. Clickable cells are recorded by the rows that draw them (`draw_editor` / `theme_lines` / `setup_lines` take `hits=`), never recomputed
- Frame mounts and queries scope to the stack bottom (`_editor_screen`): `App.query` spans every screen while `App.mount` targets the active one, so an unscoped redraw under a modal tears the editor's widgets out of the default screen and mounts them into the popup. Popups mount through their own screen methods, which are already scoped.
- An empty library opens on the first-run choice as a modal popup, not an editor with nothing to save to: `enter_setup` arms it, `SetupScreen` shows `setup_lines`' rows in a centered dialog over the dimmed editor (the screen keeps the modal dim, never an opaque fill), and `apply_key` still owns every key. `Enter`/`i` raises `import_pending` (opened after dismiss, so modals never stack); `n` opens an inline naming field typed in the popup — the terminal is never handed back — and Enter creates through the prompt-free `_setup_create`. Esc backs out of naming, quits from the choices. A popup closed still empty puts the choice back up.
- The themes list is the same kind of popup: `ThemesScreen` shows `theme_lines`' rows in a centered dialog over the dimmed editor (the screen keeps the modal dim, never an opaque fill), and `apply_key` still owns every key. Closing is the state going `overlay is None` (Enter picks a theme, Esc/`t` puts the editor back); popups never stack, and the editor behind stays as it was.
- The import popup is the same kind of popup, bigger: `ImportScreen` shows list plus preview plus footer in one centered bordered dialog over the dimmed editor (the screen keeps the modal dim, never an opaque fill), positioned with explicit margins and a live `_origin`. Behaviour stays in `import_state`; popups never stack, and the editor behind stays as it was.
- The editor footer is a docked `Footer` widget, not frame rows: `editor_hints(mult, undo)` is the one pair source `draw_editor` folds headless and `HueFooter` shows docked (height 1, overflow scrolls), and `draw_editor` / `frame_rows` take `footer=False` from the compositor so the frame lays out the rows above the bar. Clicks stop on the keys (chrome selects nothing), status stays `state.status`, and the bar hides at the size floor where no row exists for it. The import dialog's footer is the same bar in-flow (`dock: none`, buttons stay their own row), and so are the Themes/Setup bars: `theme_lines` / `setup_lines` take `footer=False` in the dialogs (headless keeps the folds), the bar carrying the pairs plus `state.status`. Dialog bars fold by width (`_fold_footer`: one row if it fits, else two, status last and never dropped); the editor bar stays one scrolling row.

## Architecture

Data flows one way:

```
terminal config → canonical slots → edit buffer → truth file → push to terminal
```

| Module | Owns |
| --- | --- |
| `huebox/color.py` | 22-slot model, hex/rgb/hsv maths, luminance, step sizes |
| `huebox/formats/` | `base` (rule machinery, flat read/write) + `ghostty`, `kitty`, `alacritty`; registry in `__init__` |
| `huebox/providers.py` | provider theme dirs, `FORMATS` reads, slugify mapping |
| `huebox/detect.py` | probes and env overrides, candidate paths, Ghostty `config-file` includes / `theme =` reads, pointer writer, `resolve()` |
| `huebox/themes.py` | home, `state.toml`, theme files, canonical writer + subset reader, `push`, post-push reload, Ghostty native export, `RAMP` |
| `huebox/render.py` | `clip` / `pack` / `visible`, frame typography (`chrome` / `title` / `wordmark`), samples, static preview, examples strip, live diff |
| `huebox/preview.py` | shared palette/strip/sample units the frame and the popup both call |
| `huebox/tui.py` | `term_size`, and nothing else: Textual owns input, resize and raw mode |
| `huebox/editor.py` | the session: `EditorState`, `apply_key`, the picker (`theme_lines`; `ThemesScreen` shows its rows), the first-run setup choice (`setup_lines`, `enter_setup`; `SetupScreen` shows its rows), staged save, `report_session` |
| `huebox/import_state.py` | headless import cursor, selection, confirm mapping |
| `huebox/app.py` | the Textual shell: one widget per block over `render`'s rows; keys, focus, resize, click and wheel |
| `huebox/cli.py` | argparse, dispatch, theme commands, exit codes; `main()` |

Dependency rule, no exceptions: `color` and `tui` import nothing intra-package;
`formats` imports `color` only; `providers` imports `formats` only;
`detect` imports `formats`;
`themes` imports `color` + `formats` + `detect`; `render` imports `color`;
`preview` imports `render` + `color`;
`editor` imports `render` + `tui` + `color` + `preview`;
`import_state` imports `providers` only;
`app` imports `render` + `tui` + `color` + `editor` + `preview` + `import_state`; `cli` imports everything.
No cycles.

## Coding Notes

- **Line-level writes only.** Never re-serialise a terminal config; only colour tokens are replaced. Compare first, write second: a no-op write is byte-identical and never opens the file.
- **Theme files are ours; configs are theirs.** `themes/` may be rewritten freely (canonical layout, via `.tmp` + rename, never partially written); terminal configs follow line-level discipline. A theme's colours never land in a file that belongs to another theme.
- **Truth first, never rollback.** A save writes the theme file and only then pushes; a failed push is a report plus exit 1, and the theme file stays as written. Ctrl+A is the one sanctioned bypass: an explicit preview push of a dirty buffer, truth untouched by construction. In direct mode the config is the truth, so Ctrl+A saves there — there is nothing to preview past the file.
- **Readers use `with open(...)`.** No bare `open()` in `huebox/` or `tests/` — the suite must be clean under `-W always`.
- **3.9-compatible code.** No `match`, no `tomllib`, no runtime `X | Y` (keep `from __future__ import annotations` in every file).
- **Errors to stderr, prefixed `huebox: `, exit 1.** No tracebacks for user errors: missing config, bad theme name, no colours found.
- **One handler per key.** A key a focused widget binds is the widget's business; the app's `on_key` steps over it. One widget acts per press.
- **Focus after mount, size from the compositor.** `mount` is a request, so focus only after the tree exists (`call_after_refresh`); `self.size` during `on_resize` is still the old size. The frame is laid out from the compositor's size, passed down as `draw_editor`'s `size=` — one size, one number.
- **Layout goes through `clip` / `pack`.** No widget measures width itself; below `MIN_COLS`×`MIN_ROWS` (in `tui.py`) the editor draws the too-small hint and nothing else.
- **Frame text is chrome, not a terminal attribute.** Paint the frame's own text (keys, labels, headers, paths) from the buffer via `render.chrome()` / `title()` / `wordmark()`, never with `DIM`.
- **Tests stay green:** `python3 -m unittest discover -s tests`, clean under `-W always`. Mirror the module under test (`tests/test_<module>.py`); new behaviour needs a case, a new format needs round-trip plus byte-identical no-op cases. `pyte` is the `test` extra. The colour-closure matrix lives in `docs/001-spec/migration-archive/test_closure.py` as a manual check, not part of the default suite.
- **Runtime deps are Pygments + Textual.** Both are required (the editor is a Textual application); only the test tooling (`pyte`) is optional.
