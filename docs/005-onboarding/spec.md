# 005 Onboarding — empty terminal opens the TUI

Status: draft. Plan and tasks live beside this spec.

## 1. Problem

A first launch from a terminal with no config file holding colours exits with `found no terminal config with colours`. Ghostty runs colourless by default, so a missing `~/.config/ghostty/config` is the common case, not an edge. The user gets no path to create or import a theme.

## 2. Goals

- `huebox` with no arguments always opens the TUI, never errors on an empty terminal.
- The TUI detects the current terminal, then shows a `no theme detected` popup with two actions: Create new and Import themes.
- Create new offers the bundled themes (Plain-light, Plain-dark, Nightspice). Picking one copies it into the library as truth and opens it for editing.
- Import themes opens the existing import popup, with a `huebox bundled` group added. Aborting the import returns to the `no theme detected` popup.
- Editing works with no terminal config on disk. The buffer is backed by the library theme.
- Save and apply ensure the terminal config: create the file when missing, add the theme pointer when the file holds no colours, then run the existing save/push path.

## 3. Non-goals

- No system light/dark auto-detect. The user picks.
- No new CLI flags. `--format` and `--config` keep working as explicit overrides.
- No Windows work. macOS and Linux only.
- No visual redesign. The popup follows the existing dimmed-modal rule.

## 4. Bundled themes

Decision: bundled inside the binary for now. Source files live at `rust/src/bundled/plain-light.toml`, `plain-dark.toml`, `Nightspice.toml`, embedded with `include_str!`. Each file is a canonical huebox theme (22 slots) and doubles as the golden copy for tests.

`library::bundled::{list, read}` exposes them. The import popup gains a `huebox bundled` group backed by this seam, read-only. Create and import both copy through the existing `themes::create` facade, so `~/.config/huebox/themes/` stays the truth. Nothing ever writes back into the bundle.

Plain-light and Plain-dark use standard expected colours for their mode. Nightspice ships as the accent carryover.

## 5. Launch flow

`cli` resolves the terminal before opening the shell, using the existing `detect` order. When no candidate config holds colours, it opens the editor in a no-target session: library theme loaded (or none yet), terminal name known, config path known-or-default.

`EditorState` gains the no-theme branch. `apply_key` owns every key. `app` renders `PopupKind::NoTheme` as a centered dialog over the dimmed editor, never stacked, never opaque.

- Create new lists the three bundled themes. Enter copies the highlighted one into the library through the injected writer and loads it into the buffer. Esc returns to the two choices.
- Import themes raises a pending import, opened after the no-theme dialog dismisses so modals never stack. A confirm copies through `themes::create` and loads the buffer. An abort with an empty library puts the no-theme dialog back up.
- The dialog closes only when the buffer holds a library theme. Closing quits the session when the library is still empty.

## 6. Save and apply without a config

`editor` stays headless: it never touches the terminal config. `cli` builds a new `EnsureTargetFn` seam beside `WriteFn`/`ApplyFn` and hands it to the save/apply path.

On save or apply with a known terminal and theme:

- When the default config path has no file, create parent dirs and an empty file, then run the existing `themes::save` plus `push` path.
- When the file exists but holds no colours, append the pointer line, then run the existing path. Ghostty uses the existing `ensure_theme_pointer` (`theme = <name>`). Kitty and alacritty use their own existing push entry: the push writes the colour lines, so an empty created file is enough input.
- Ghostty native export stays the mechanism: the library theme is truth, the export into the Ghostty themes dir plus the pointer line is the push. No new writer, no invented key.
- Failures return status strings through the existing outcome shape. The buffer keeps its clean/dirty semantics: save moves `saved`, apply never does.

## 7. Architecture touchpoints

- `library::bundled` is new. `library::themes` and `library::detect` are unchanged except for the ensure-file helper, which lives behind the facade.
- `editor` gains the no-theme overlay state and the bundled-picker cursor. `ImportState` gains the bundled group through the injected `ImportLibrary`.
- `app` gains `PopupKind::NoTheme` rows plus the bundled list rows. No new stacking behaviour.
- `cli` wires `EnsureTargetFn`, preseeds the no-target session, and passes the bundled reader into the import seam.
- Dependency rule holds: `editor` on `core` + `render` only, `app` on `core` + `render` + `editor`, `cli` on everything.

## 8. Tests

- Bundled golden copies parse to 22 slots and round-trip byte-identical through `themes::save`.
- Editor unit cases: empty session shows the dialog, create copies and loads, import abort returns, dialog close with empty library quits.
- Ensure-target cases: missing file created, colourless file gains the pointer, existing colours untouched.
- Pty replay: empty-home launch reaches the dialog; create-then-save leaves a config holding colours. Frozen oracle seed writer stays unchanged in shape.

## 9. Gate

Empty-home launch opens the dialog on ghostty, kitty, and alacritty. Create-then-save produces a config holding colours on each. Full gates green: `cargo test`, clippy `-D warnings`, `cargo fmt --check`, pty harness.
