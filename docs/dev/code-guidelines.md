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
- `core` depends on no other module; everything else depends only on what the rule allows
- One crate (`huebox`), seven modules — the workspace merge kept every boundary as module discipline, not compiler enforcement: a `use` that breaks the rule fails review, not the build

### 2. Facade Pattern
Shields callers from subsystem complexity; lets internals evolve without breaking consumers.
- `format::lookup` / `read` / `write` is the facade for terminal formats — the push reuses it, never invents a key
- The provider seam in `library` is the facade for provider themes — the import popup lists and reads through it, never a directory walk of its own
- `library::themes::{save, push}` is the facade for writing theme files and pushing to terminals; `create` is the facade for new library files — the import confirm writes through it, never a file of its own. `ghostty_overrides` / `clear_ghostty_overrides` is the facade for Ghostty's inline shadows — callers never match colour lines themselves, and `push` reports them as `PushResult.overrides
- `library::detect::resolve()` is the facade for finding the push target — the CLI and preview resolve through `resolve()`, the push through `resolve_for_push()` (same triple, `/etc` excluded)
- `app` / `cli` never reach into format internals or config parsing directly — use the facades

### 3. Dependency Injection
Decouples the editing session from the outside world; makes the session testable without a compositor.
- `editor` reaches outside only through injected seams that `cli` builds: the save seam (`WriteFn`) and the preview seam (`ApplyFn`) — each handed the subject every time, since a picker switch retargets it mid-session — the Ghostty delete seam (`DeleteFn`) behind the overrides warning, `PromptFn` (one prompt pattern), the `Library` trait behind the theme picker, and the import popup's `ImportLibrary` plus its writer
- `editor` depends on `core` + `render` only — it never touches `themes` or `detect` to save a parameter
- The import flow follows the same rule: `ImportState::new` takes an injected `ImportLibrary`, `app` takes an injected writer — neither imports `themes` or `detect`
- `cli` is the composition root: it builds every seam the editor and the shell run through, so tests drive a session without a compositor
- Commands that never open the editor never pay for the shell

### 4. State/View Separation
Separates editing behaviour from the compositor; enables testing behaviour without the shell.
- `EditorState` + `apply_key` own ALL editing behaviour — selection, adjust, undo, revert, step size, picker, first-run setup choice, Ghostty overrides warning, save, apply, two-armed Esc. `app` owns only keys-in, redraw, and the prompts
- `app` renders `draw_editor`'s rows — it never re-renders the frame, re-implements grid geometry, or duplicates step arithmetic (`HUE_STEP` / `CHANNEL_STEP` in `core` is the single source)
- The editor draws from the in-memory buffer every frame; truth reaches disk on Ctrl+S only, a preview push on Ctrl+A only — and the preview never moves `saved`, so the buffer stays dirty and Esc keeps guarding it
- A click resolves to a slot and goes through `apply_key` — it never touches a colour or a frame directly. Clickable cells are recorded by the rows that draw them (the frame returns `hits` with its rows, clicks resolve through `slot_at`), never recomputed
- Popups draw through their own `PopupLayout` over the dimmed editor — the editor's rows are never reparented into a popup.
- An empty library opens on the first-run choice as a modal popup, not an editor with nothing to save to: `enter_setup` arms it, `PopupKind::Setup` shows its rows in a centered dialog over the dimmed editor (the shell keeps the modal dim, never an opaque fill), and `apply_key` still owns every key. `Enter`/`i` raises a pending import (opened after dismiss, so modals never stack); `n` opens an inline naming field typed in the popup — the terminal is never handed back — and Enter creates without a prompt. Esc backs out of naming, quits from the choices. A popup closed still empty puts the choice back up.
- The themes list is the same kind of popup: `PopupKind::Themes` shows the picker rows in a centered dialog over the dimmed editor (the shell keeps the modal dim, never an opaque fill), and `apply_key` still owns every key. Closing clears the state's overlay (Enter picks a theme, Esc/`t` puts the editor back); popups never stack, and the editor behind stays as it was.
- The Ghostty overrides warning is the same kind of popup: a save or an apply that shadowed its own export arms it (`WriteOutcome.overrides` carries main + keys), `PopupKind::Overrides` shows its rows in a centered dialog over the dimmed editor (the shell keeps the modal dim, never an opaque fill), and `apply_key` still owns every key. Closing clears the state's overrides (`y`/Enter on delete removes the lines through the injected deleter, `n`/Esc keeps them); popups never stack, the buffer stays as the save left it (clean) or the apply left it (dirty), and the editor behind stays as it was.
- The import popup is the same kind of popup, bigger: `PopupKind::Import` shows list plus preview plus footer in one centered bordered dialog over the dimmed editor (the shell keeps the modal dim, never an opaque fill). Behaviour stays in `ImportState`; popups never stack, and the editor behind stays as it was.
- The editor footer is a docked bar, not frame rows: `editor_hints` is the one pair source (keys only — the step multiplier stays out, the status line confirms it), the frame folds headless and the shell docks the bar (height 1, overflow scrolls) so the frame lays out the rows above it. Clicks stop on the keys (chrome selects nothing), status stays the state's, and the bar hides at the size floor where no row exists for it. The import dialog's footer is the same bar in-flow (buttons stay their own row), and so are the Themes/Setup/Overrides bars: the bar carrying the pairs plus the status. Dialog bars fold by width (one row if it fits, else two, status last and never dropped); the editor bar stays one scrolling row.

## Architecture

Data flows one way:

```
terminal config → canonical slots → edit buffer → truth file → push to terminal
```

| Module | Owns |
| --- | --- |
| `core` | 22-slot model, hex/rgb/hsv maths, luminance, step sizes |
| `format` | format rules + `lookup` registry: ghostty, kitty, alacritty |
| `library` | providers, `detect` (probes, includes, `theme =` reads, pointer writer, `resolve()`), `themes` (home, `state.toml`, save/push/create, Ghostty native export, overrides), reload |
| `render` | `clip` / `pack` / `visible`, frame typography (`chrome` / `title` / `wordmark`), samples, static preview, examples strip, live diff |
| `editor` | the session: `EditorState`, `apply_key`, picker/setup/overrides/import state |
| `app` | the Ratatui shell: frame, popups, prompts, mouse |
| `cli` | clap surface, dispatch, theme commands, exit codes; `main()` |

Dependency rule, no exceptions: `core` stands alone;
`format` and `render` build on `core` only;
`library` on `core` + `format`;
`editor` on `core` + `render`;
`app` on `core` + `render` + `editor`;
`cli` on everything.
No cycles.

## Coding Notes

- **Line-level writes only.** Never re-serialise a terminal config; only colour tokens are replaced — the two exceptions are the overrides delete, which removes whole colour lines and leaves every other byte identical, and the missing-key insert (decision 57), which appends one line per added key after the last colour line (flat) or under its `[colors.*]` table (TOML) and leaves every other byte identical. Compare first, write second: a no-op write is byte-identical and never opens the file.
- **Theme files are ours; configs are theirs.** `themes/` may be rewritten freely (canonical layout, via `.tmp` + rename, never partially written); terminal configs follow line-level discipline. A theme's colours never land in a file that belongs to another theme.
- **Truth first, never rollback.** A save writes the theme file and only then pushes; a failed push is a report plus exit 1, and the theme file stays as written. Ctrl+A is the one sanctioned bypass: an explicit preview push of a dirty buffer, truth untouched by construction. In direct mode the config is the truth, so Ctrl+A saves there — there is nothing to preview past the file.
- **Errors as values on user paths.** Fallible file and parse work returns `Result`, never panics — `cli` carries zero bare `unwrap`, and every failure surfaces as `huebox: ` plus an exit code.
- **Stable Rust, edition 2021.** No nightly features.
- **Errors to stderr, prefixed `huebox: `, exit 1** (usage errors exit 2 as `huebox: error: ...`). No tracebacks for user errors: missing config, bad theme name, no colours found.
- **One handler per key.** A key the popup consumes never reaches the editor behind it. One handler acts per press.
- **Layout goes through `clip` / `pack`.** Nothing measures width itself; below `MIN_COLS`×`MIN_ROWS` the shell draws the too-small hint and nothing else.
- **Frame text is chrome, not a terminal attribute.** Paint the frame's own text (keys, labels, headers, paths) from the buffer via `chrome()` / `title()` / `wordmark()`, never bare.
- **Tests stay green:** `cargo test --workspace`, clean under `cargo clippy --workspace -- -D warnings` and `cargo fmt --check`. Mirror the module under test (`rust/tests/<name>.rs`); new behaviour needs a case, a new format needs round-trip plus byte-identical no-op cases. The pty harness (`tests/test_pty_rust.py`) replays the parity session against the release binary on a frozen oracle — its seed writer must never change shape. The colour-closure matrix lives in `docs/001-spec/migration-archive/test_closure.py` as a manual check, not part of the default suite.
- **No runtime dependencies.** One static binary; only the pty harness needs anything extra (`pyte`, carried by the dev `.venv`).
