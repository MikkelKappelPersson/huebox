# huebox 003 — TUI import menu

Status: **draft** · Format version: **1** · Last updated: 2026-10-08

This is the spec for the in-editor theme import flow. It describes *what*
the import menu does, not *how* it is built: there is deliberately no
implementation plan here yet. That comes after this document settles.

`docs/001-spec/spec.md` stays the authority for everything it already
covers (colour model, write contract, picker, layout budget). This document
only adds the import surface. Where the two disagree about shared preview
blocks, the shared-module rule in §6 wins and `001` gets a guideline
update, not a silent fork.

Tests are deferred until the full spec is implemented (agreed for this
spec). §10 records what the harness will eventually cover, nothing more.

---

## 1. Problem

Getting a theme *into* huebox today means leaving the editor: `huebox
import <name>` snapshots the detected terminal config, and anything else
is hand-copying colours. A user staring at Ghostty's 300+ shipped themes,
kitty's `kitten themes` collection, or Alacritty's theme repo has no way
to browse them, compare them against their own palette, and pull several
into the library without dropping back to a shell per theme.

## 2. Goals

- Open an import popup from inside the editor, without leaving the session.
- Browse provider themes grouped by origin (Ghostty themes, kitty themes,
  Alacritty themes, …), one expandable group per provider.
- Click and/or arrow through the list; toggle multiple themes; see each
  candidate rendered in huebox's own preview blocks before committing.
- Confirm once: every selected theme lands in `~/.config/huebox` as a
  first-class theme, importable, editable, and pushable like any other.
- Reuse the editor's own preview rendering for the popup preview —
  no second implementation of what a theme looks like.

## 3. Non-goals

- Not a theme store, gallery, or sharing service (§2 of `001` still holds).
- Not a network client: no fetching, no browsing remote repos at runtime.
  Providers read what is already on the machine (see §8 open question 1).
- Not a config editor and not a push flow: importing writes library files
  only, never touches a terminal config and never changes the current theme.
- Not a general file picker: no arbitrary-path loading in this spec.

## 4. User-visible surface

### 4.1 Entry: an `Import` button below `Themes`

- The compositor top's info column owns two stacked controls: the existing
  `Themes` button and, directly below it, an `Import` button.
- Same look and contract as `Themes`: flat, never focusable (arrows stay on
  the grids), painted only in the theme's own
  `selection-background` / `selection-foreground`, hover as underline.
- Pressing it (click) does exactly what the `I` key does (see §4.6),
  through the same call — the button and the key cannot disagree about what
  the import popup is, the same rule `Themes`/`t` follows.
- `HUEBOX_EDITOR_PANEL=0` (bare stack top) keeps no `Import` button; `I`
  still opens the popup. The button costs no extra row: it rides the info
  column's existing blank-fill row the same way `Themes` does.

### 4.2 The popup: list left, preview right, confirm footer

- Opening `Import` suspends the editor frame and mounts a modal popup in
  its place (same surface-takeover rule as the theme picker, decision 19
  in `001`): one frame, one layout budget, no centred box over the editor.
- Layout inside the popup, at every size it fits:
  - **Left:** the provider groups and their theme lists (§4.3).
  - **Right:** the preview of the highlighted (cursor) theme (§4.5).
  - **Footer:** hint line plus confirm/cancel — `space` toggle,
    `Enter`/confirm button imports, `Esc`/`I` backs out without writing
    anything.
- Below the minimum size the popup keeps the editor's own fallback: the
  `terminal too small` line instead of a squeezed modal. Same `MIN_COLS` /
  `MIN_ROWS` floor, no second constant.
- `Esc` (or `I`, or the cancel button) always backs out without writing.
  Opening the popup is never blocked by a dirty buffer, and closing it —
  either way — never touches the buffer: importing adds library files, it
  does not retarget the session the way the picker does.

### 4.3 Provider groups are `Collapsible`s, themes are a multi-select list

- Each provider is one Textual `Collapsible` (see §5): title is the provider
  name plus count (`Ghostty (142)`), body is that provider's theme list.
- At least one provider starts expanded; collapsing all is allowed (the
  footer still names what is selected). Empty providers render their title
  with a `0` count and one muted `no themes found` row — never a bare
  missing block.
- Themes inside a provider are rows in a multi-select list: each row shows
  a toggle mark plus the theme name. The cursor (highlight) and the
  selection (toggled) are visually distinct — moving is not picking.
- Keyboard moves within and across groups: up/down moves the cursor one
  theme; left/right (or clicking a group title) collapses/expands the
  group; the cursor crossing a group boundary enters the next open group.
  A collapsed group's selected themes stay selected — hiding is not
  deselecting — and the footer count is the proof.
- Mouse: click a group title toggles the group; click a theme row moves the
  cursor there *and* toggles it (mirroring the picker's click-is-Enter
  rule: one call, not a second implementation); wheel over the list moves
  the cursor so the window follows. Clicking chrome (borders, gaps,
  preview) selects nothing.

### 4.4 Multi-select, then confirm

- `space` toggles the highlighted theme; `a` selects all visible
  (all open groups — decided §8.3); `n` clears.
  The footer always shows the live count: `3 selected — Enter imports,
  Esc cancels`.
- Confirm is `Enter` on the footer button or the `Enter` key when the popup
  has focus (see §4.6 for the cursor-vs-confirm disambiguation). Confirm
  with nothing selected is a no-op that stays in the popup and says so
  (`nothing selected` in the footer), not an error and not a close.
- On confirm the popup closes and the editor frame returns with a status
  line naming what landed (`imported 3 themes: …`) or why nothing did.
  Failures that cannot go on one line (unreadable source, bad name,
  unwritable library) go to the session notes, printed at exit like the
  picker's notes — never a modal inside the popup, same rule as decisions
  10/21 in `001`.

### 4.5 Preview: the highlighted theme, in shared blocks

- The right column previews the **highlighted** (cursor) theme, not the
  selection set: moving the cursor repaints the preview on the same frame.
- Preview content, top to bottom:
  1. Theme name (bold `foreground` header, same as other block titles).
  2. A small palette rendering — all 16 slots readable at once.
  3. The interface-text examples strip (background / selection / cursor
     pairs).
  4. The live code sample below, space permitting (first to shed rows on
     narrow/short popups, same shedding order as §15: strip keeps its
     floor, sample tail goes first).
- Every colour in the preview comes from the highlighted theme's slots;
  chrome (name, header, footer) follows the frame-typography rule (§8.1 of
  `001`). No cell shows the terminal's background; the popup background is
  the previewed theme's own `background`, same floor rule as §8.2 of `001`.
- The preview is read-only: no editing, no selection of preview text in
  this spec (text selection stays where `001` put it: editor sample/diff
  only).

### 4.6 Keys

| Key | Action |
| --- | --- |
| `I` | open the import popup / close it (same as `Esc`, writes nothing) |
| `up` / `down` (arrows) | move the cursor one theme, across open groups |
| `left` / `right` | collapse / expand the group under the cursor |
| `space` | toggle the highlighted theme |
| `a` / `n` | select all visible / clear selection |
| `Enter` | import the selected themes and close (no-op + footer note when empty) |
| `Esc` | close, import nothing |

- One handler per key (coding-notes rule): while the popup owns the surface,
  editor colour keys (`w/e/s/d/x/c/f/i/u/r/t/N/…`) are inert — they neither
  edit nor collapse. `Ctrl+C` takes the same path as `Esc`.
- `Enter` never both toggles and confirms: on a theme row it confirms the
  whole selection set (toggle is `space`'s job alone), so keyboard and
  click confirm through the same call.

### 4.7 Focus

- The popup takes focus while it is up (same rule as the picker): focus
  left on a grid underneath would let arrows move the *colour* selection
  behind a list the user is reading.
- Inside the popup, focus lives on the list; the confirm/cancel footer
  answers to its keys without holding focus, so tab-trapping is impossible.
  On close, focus returns to the grid holding the colour selection.

## 5. Textual widgets (from the gallery)

Use Textual's own widgets throughout — no hand-rolled list, toggle, or
modal. Researched against <https://textual.textualize.io/widget_gallery/>:

| Need | Widget | Why it |
| --- | --- | --- |
| provider group | `Collapsible` | title click toggles, open by default, already theme-closed in `Live`; one per provider |
| theme multi-select | `SelectionList` | built for toggle-multiple: `space` toggles, `selected` is the set, `SelectedChanged` drives the footer count and preview repaint; one per provider group (or one list with separators — §8 question 2) |
| entry + confirm/cancel | `Button` | the `Import` entry mirrors `ThemesButton` (flat, unfocusable); footer confirm/cancel are ordinary focusable buttons |
| preview blocks | shared modules (§6), mounted in `Static`/`Vertical` | preview content is huebox rows, not Textual controls — `Static` is the neutral carrier |
| popup frame | `Screen`-level modal or mounted panel | whichever Textual idiom the picker-to-widget extraction (\(001\) §5.6) already uses for surface takeover — follow that, don't invent a second modal pattern |

`OptionList` underlies `SelectionList`; use it only through
`SelectionList`'s API (bindings, `selected`, messages), not beside it.
`DataTable`, `DirectoryTree`, ` Tree`, `Tabs` are explicitly out: this is a
grouped toggle-list with a preview, not a table or a file tree.

## 6. Shared preview modules (no copy-paste)

The popup preview and the editor frame must render from the same code:

- The examples strip (`example_lines`), the code sample (`sample_lines`),
  and the palette swatch cell rendering are extracted (or hoisted, like
  `swatch_cell` / `named_cell` were) into importable units the editor frame
  *and* the import preview both call — with slots passed in, colours read
  per call, no stored theme.
- One implementation of each block, asked from two layouts. A preview that
  re-implements the strip, the sample token→slot mapping, or the swatch
  geometry is a second description of the layout, and the two will drift
  the way `frame_hits` was created to prevent.
- The small 16-slot palette rendering is part of this contract: whatever
  compact form the popup uses (grid, pairs, abbreviated cells) is the unit
  under test for both callers, not inline paint in the popup.
- Theme-closed like everything else: the popup's own chrome (group titles,
  toggle marks, footer, borders) binds only the design tokens `TOKEN_SLOTS`
  already maps, and the colour-closure harness (§10) will reject anything
  else when it is extended.

## 7. Import semantics (what confirm does)

- Each selected provider theme becomes a huebox theme via the existing
  `themes.create` facade — the popup never writes library files directly.
  Import records the origin as the theme's `source` (`ghostty:<path>`,
  `kitty:<path>`, …), the same informational field `import` writes today.
- Import writes truth files only: no push, no retarget of the session, no
  change to the current theme, no touch of the edit buffer. A save or `use`
  afterwards pushes like any other theme.
- Name mapping: provider theme names are slugified into valid huebox names
  (`themes.valid_name`; letters, digits, `-`, `_`, 64 max). The exact
  mapping (case, spaces, duplicates across providers) is §8 question 4.
- Clashes: a selected name that already exists in the library is skipped
  and named in the post-import report (`already in library: …`), never
  silently overwritten. No `--force` in the popup; overwrite stays a CLI
  verb (`import --force`), same way the picker confirms in text rather
  than with a modal.
- Provider read failures (missing dir, unreadable file, zero colours in a
  source file) never abort the whole confirm: the readable themes land, the
  failures are reported per theme in the notes. A provider with nothing
  readable renders as the empty group in §4.3.

## 8. Open questions — settled 2026-10-08

1. **Where do provider themes come from?** Decided: **A — fixed local
   dirs per terminal** (e.g. Ghostty resource + `~/.config/ghostty/themes`,
   kitty themes dir / kitten cache, Alacritty themes dir). No subprocess,
   no network, testable. Exact paths per provider belong to the plan.
2. **One `SelectionList` per provider or one list with separators?**
   Behaviour decided, implementation deferred to the plan spike: the user
   sees **one continuous list** — one cursor walks up/down through every
   open group, `space` toggles across providers (Ghostty a/b/c plus kitty
   a, one confirm takes all), collapsed groups are skipped, never
   deselected. Whether that is one `SelectionList` with group rows or one
   per `Collapsible` with shared cursor state is a plan-level pick after a
   Textual spike.
3. **`a` selects what?** Decided: **all visible (open groups)** — what you
   see is what `Enter` takes.
4. **Name mapping details.** Decided: **slugify + suffix in-batch, skip
   against the library** — lowercase, runs of non-alphanumerics to `-`,
   truncate 64; within-batch collisions take `-2`, `-3`; a name already in
   the library skips with a report (§7). No provider prefix, no prompts.
5. **Preview of multiple selections?** Unchanged: cursor theme only; the
   footer carries the count. Anything richer is a later spec.

## 9. Architecture constraints (from code-guidelines.md, binding)

- **Modular design:** the import list, the provider readers, and the shared
  preview units are separate modules with one job each; no cycles.
- **Facade pattern:** the popup reads provider themes through a provider
  registry (same shape as `FORMATS`) and writes through `themes.create` —
  it never reaches into format internals or theme-file layout.
- **Dependency injection:** the editor reaches the import flow only through
  injected callables the `cli` composition root builds (list/load/create
  per provider), the same way the picker receives its `Library`. Never
  import `themes` / `detect` into the import state or the editor to save a
  parameter.
- **State/view separation:** import behaviour (cursor, expanded groups,
  toggled set, confirm mapping) lives in a headless state + key/message
  surface of its own, testable without a compositor — the same split
  `EditorState` + `apply_key` vs `app` keeps. The popup widget is a thin
  shell over that state plus the shared preview units.

## 10. Testing (deferred, recorded for later)

When the full spec is implemented, the harness extends to: popup open/close
(keys and both buttons), cursor movement within and across groups
(including collapsed boundaries), toggle/untoggle and the footer count,
cursor-preview repaint identity (preview shows the cursor theme's slots),
confirm writes (files created, sources recorded, clashes skipped, buffer
and current theme untouched), empty-provider and empty-selection cases,
too-small fallback, and I2 colour-closure over the popup. Nothing in this
list gates the spec itself.

## 11. Decisions

Append-only. Newest last. One line per decision, with the reason.

| # | Decision | Why |
| --- | --- | --- |
| 1 | `Import` button sits below `Themes`, same contract (flat, unfocusable, selection-pair paint) | One column owns the top's controls; a second button anywhere else splits the surface `t`/`I` share |
| 2 | Providers are `Collapsible`s, themes a `SelectionList` multi-select, confirm once | Textual-native grouped toggle-list; matches the requested shape without inventing widgets |
| 3 | Preview shows the cursor theme via shared `example_lines` / `sample_lines` / swatch units | No second renderer; the editor and the popup cannot disagree about what a theme looks like |
| 4 | Confirm writes truth files only — no push, no retarget, current untouched | Import is library work, not session work; push stays on save/`use` where the write contract already lives |
| 5 | Clashes skip with a report; no force in the popup | Same rule as the picker's text-confirm: no modal overwrite inside a surface the user is reading |
| 6 | Tests deferred until the full spec is implemented | Spec-first: pin behaviour in prose, prove it in the harness once the flow exists end to end |
| 7 | Providers read fixed local dirs per terminal (§8.1) | No-network rule leaves no other source; subprocess parsing buys failure modes for nothing |
| 8 | One continuous cursor across open providers; collapsed skipped, never deselected (§8.2) | Cross-provider pick-then-import-once is the flow; widget count is plan detail |
| 9 | `a` selects all visible — every open group (§8.3) | What you see is what `Enter` takes |
| 10 | Slugify, `-2`/`-3` in-batch, skip against the library (§8.4) | Deterministic, no prompts, no noisy provider prefixes |
