# huebox 003 — implementation plan

Status: **draft** · Plans spec: `spec.md` §§1–11 · Conventions: `AGENTS.md` + `docs/dev/code-guidelines.md` · Next: `tasks.md` (separate, after this settles)

This turns the import-menu spec into build order. The spec says *what*;
this says *how*, module by module, phase by phase. No task list lives here —
`tasks.md` comes afterwards. Everything here keeps `python3 -m unittest
discover -s tests` green; per the spec, dedicated import-harness cases land
with the full implementation, not phase by phase.

| Phase | Delivers | Spec | Touches |
| --- | --- | --- | --- |
| 0 | Textual spike: list structure + screen pattern | §5, §8.2 | throwaway only |
| 1 | Provider registry (local dirs, `FORMATS` reads) | §§7–8.1 | new `providers.py`, `detect.py` seams |
| 2 | Shared preview units (palette + strip + sample) | §6 | `render.py` / `editor.py`, new preview unit |
| 3 | Headless import state + confirm mapping | §§4.3–4.4, §9 | new `import_state.py` (name TBD) |
| 4 | Popup shell (button, takeover, list, preview, footer) | §§4–5 | `app.py` (+ tokens/CSS) |
| 5 | Composition-root wiring + confirm report | §7, §9 | `cli.py`, `app.py`, `themes.py` facade only |

Research backing this plan lives in `research.md` (widget APIs, message
flow, spike script). Read it alongside phases 0 and 4.

---

## Phase 0 — Textual spike (throwaway, decides §8.2)

Goal: answer the one question the spec deferred — one `SelectionList` per
provider vs. one list with group rows — with a running spike, not a vote.
Nothing here ships; the spike is deleted (or archived outside `huebox/`)
once the pick is recorded.

### 0.1 What to build

A minimal Textual app with three `Collapsible`s (Fake Ghostty / kitty /
Alacritty, ~30 rows each) and both candidate wirings behind an env flag.
Measure only what the spec constrains: one cursor walks up/down through
every *open* group, `space` toggles across providers, collapsed groups are
skipped never deselected, cursor move repaints a preview pane, `SelectedChanged`
drives a footer count. See `research.md` §2 for the starter script.

### 0.2 Candidate A — one `SelectionList` per `Collapsible`

Each provider owns its list; a headless cursor (phase 3 shape) owns the
cross-list order and each list's `highlighted` is set programmatically as
the cursor moves. Expected friction: moving highlight across widget
boundaries by hand; keeping `SelectedChanged` per list merged into one
footer count; focus staying on "the list" while there are N lists. Upside:
collapse is free (native `Collapsible`), no custom non-selectable rows.

### 0.3 Candidate B — one `SelectionList` with group rows

One list, provider headers as disabled separator rows (or `Separator`s).
Expected friction: headers must be un-highlightable and un-toggleable —
`SelectionList` inherits `OptionList` navigation (`up/down/home/end`,
`enter` selects), so every navigation path must skip headers; collapse
means removing/re-adding rows rather than toggling a container. Upside: one
cursor, one `selected`, one message stream for free.

### 0.4 Screen pattern spike (same sitting)

`ModalScreen` vs. the picker's mounted-takeover pattern. The picker
replaced the frame while up (spec §4.2 cites decision 19); the import popup
does the same. Spike both: `push_screen(ImportScreen())` with a
`dismiss(result)` confirm path, and a mounted-panel takeover inside
`Editor.redraw`. Judge on: focus ownership while up, `Esc`/`Ctrl+C` path,
resize/redraw behaviour, and which one leaves the picker's code path
untouched. `research.md` §3 records the API shapes; the spike records the
verdict here before phase 4 starts.

### 0.5 Exit criteria

A short note appended to `research.md`: chosen structure, cursor/focus
ownership (single focused list, focus-follows-cursor proven), `Enter`-hijack
mechanism with no double-fire, wheel verdict (custom `on_wheel` moving the
highlight vs native viewport scroll — spec wants cursor-follows-wheel),
`I` proven free in `apply_key` + grid bindings (lowercase `i` is hex entry),
message flow (`SelectedChanged`/`SelectionHighlighted` vs. state
calls), and the screen pattern — with the rejected candidate's reason in
one line. Phase 4 follows it without re-litigating.

---

## Phase 1 — provider registry (local dirs only)

Goal: a `FORMATS`-shaped facade over on-disk provider themes. No
subprocess, no network (spec §8.1, decision 7).

### 1.1 New module, one job

A new module (name TBD in tasks; `providers.py` fits the one-job-per-module
rule) owns: provider enumeration (fixed dirs per terminal), file discovery
(non-recursive, known suffixes only), reads through `FORMATS[fmt]["read"]`,
and the slugify mapping. Dependency rule: imports `color` (slug/validity
helpers) + `formats` only — never `themes` or `detect`. Path roots that live
in `detect.py` (`ghostty_themes_dir`, shipped-resources override) arrive as
injected values from `cli` (phase 5), which already imports `detect` — the
same shape as the picker's `Library`. Reuse through parameters, not imports,
so `app.py` and the headless state can consume the registry without touching
`detect`.

### 1.2 Provider table (initial, expandable)

| Provider | Format read | Dirs (in order) |
| --- | --- | --- |
| `ghostty` | `FORMATS["ghostty"]["read"]` | `GHOSTTY_RESOURCES_DIR/themes` (shipped, default `/usr/share/ghostty`), `ghostty_themes_dir()` (user) |
| `kitty` | `FORMATS["kitty"]["read"]` | user kitty themes dir / `kitten themes` cache (exact dirname pinned in tasks after a filesystem check) |
| `alacritty` | `FORMATS["alacritty"]["read"]` | user alacritty themes dir (exact dirname pinned in tasks) |

Registry shape mirrors `FORMATS`: `PROVIDERS[name]["list"]` → theme files,
`["read"]` → slots. A provider whose dir is missing/unreadable yields the
empty group (spec §4.3), never an exception into the caller. Files with
zero colours never list — skipped per-file with a note; the group still
mounts.

### 1.3 Slugify (spec §8.4)

One pure function, shared by state (phase 3) and any CLI reuse: lowercase,
runs of non-`[a-z0-9]` → `-`, strip leading `-` (so the result satisfies
`valid_name`'s `^[A-Za-z0-9]` start; leading digits are fine), truncate the
stem to leave room for a `-N` suffix within 64. In-batch collisions take `-2`,
`-3`; collisions against the existing library are *reported upstream*, not
resolved here (spec §7: skip with report). Case-insensitive comparison, same
as `themes.create`.

---

## Phase 2 — shared preview units (no copy-paste)

Goal: the popup preview and the editor frame call the same code (spec §6,
decision 3).

### 2.1 Hoist, don't fork

`example_lines`, `sample_lines`, and the swatch-cell rendering
(`swatch_cell`/`named_cell` family in `editor.py`) become importable units
with slots passed in and colours read per call. The editor frame keeps
calling them with identical output (byte-identical frames before/after —
the existing suite is the guard); the popup preview calls the same units
at its own width. The compact 16-slot palette form the popup needs is
defined once as part of this contract, not as inline popup paint.

### 2.2 Purity + theme-closure

Units stay pure (slots in, ANSI rows out — same as `example_lines` today).
No stored theme, no widget imports inside the units: `app.py` mounts the
rows in `Static`/`Vertical` carriers. New tokens, if any, extend
`TOKEN_SLOTS` with theme slots only, so the I2 closure property the popup
inherits is structural, not reviewed per row.

---

## Phase 3 — headless import state (no compositor)

Goal: import behaviour testable without Textual — the `EditorState` +
`apply_key` split, repeated (spec §9).

### 3.1 State shape

A small state object holding: provider order + per-provider theme ids,
expanded set, cursor (provider, index) over *open* groups only, toggled
set of ids, footer note, plus a slots cache (read-once per popup session:
preview lookup hits the cache, the injected reader only on first highlight).
Cursor movement, expand/collapse, toggle,
select-all-visible/clear are pure transitions on this state; preview lookup
(cursor id → slots) reads the cache, never a stored session copy.

### 3.2 Key/message surface

One function (or small message map) owns the spec §4.6 keys: `up/down`
(cursor across open groups, skipping collapsed), `left/right`
(collapse/expand under cursor), `space` (toggle), `a`/`n` (all
visible/clear), `Enter` (confirm mapping → slugified unique names +
skip-list), `Esc`/`I`/`Ctrl+C` (abandon, nothing written). Editor colour
keys are inert by construction — the surface simply has no branch for
them. Click/wheel resolve through the same transitions (move-cursor-then-
toggle), so mouse and keyboard share one implementation like the picker
does.

### 3.3 Injection + confirm mapping

The state never imports `themes` or `detect`: listing/reading come from
injected callables the `cli` root builds (phase 5), mirroring the picker's
`Library`. Confirm maps the toggled set through the phase-1 slugify
(batch `-2`/`-3`, library clashes → skip entries) and returns a plain
confirm plan + per-theme failure notes — writing itself stays in phase 5
through `themes.create`.

---

## Phase 4 — popup shell in `app.py`

Goal: the visible popup, thin over phases 1–3 (spec §§4–5).

### 4.1 Entry

`Import` button under `Themes` in the info column: same flat contract
(unfocusable, selection-pair paint, underline hover), same blank-fill row
discipline (no new row cost). `on_button_pressed` routes `import-button`
through the identical call the `I` key takes — one call, mirroring the
`Themes`/`t` rule. Bare-stack mode (`HUEBOX_EDITOR_PANEL=0`) keeps no
button; `I` still works.

### 4.2 Takeover + layout

The pattern phase 0 chose (modal screen vs. mounted takeover), applied
once — no second modal idiom beside the picker's. Inside: left list column
(phase-0 structure), right preview column (phase-2 units in
`Static`/`Vertical`), footer (hints + live count + confirm/cancel
`Button`s). Minimum-size behaviour reuses `MIN_COLS`/`MIN_ROWS` with the
same too-small line; no second constant. Short/narrow popups shed preview
rows in the spec §4.5 order (sample tail first, strip keeps its floor).

### 4.3 Focus, keys, messages

Popup takes focus while up (picker rule); inside, exactly one list holds
focus at a time and focus moves with the cursor across providers, so there is
never a split-brain N-lists focus state. Footer confirm/cancel fire via
screen-level bindings + click, never via focus cycling — tab-trapping is
impossible by construction. On close focus
returns to the grid holding the colour selection. `on_key` routes popup
keys to the phase-3 surface while up and steps over everything else (one
handler per key; editor colour keys inert). `Enter` is the known conflict:
`SelectionList` inherits `OptionList`'s `enter`-selects binding while the spec
gives `Enter` to confirm-the-set — phase 0 proves the hijack (screen-binding
precedence or `BINDINGS` override) and toggle-vs-confirm never double-firing
before phase 4 wires it. Message handlers:
`SelectionList.SelectedChanged` → footer count, `SelectionHighlighted` (or
cursor calls) → preview repaint, `Collapsible.Toggled` → expanded-set
persistence across redraws (same pattern as `Live`'s `_collapsed`). `a`
loops `select_all` over open groups only; collapsed lists stay mounted (still
in `query(SelectionList)`, selections intact) and are explicitly skipped.

### 4.4 Theme-closure

Popup chrome binds only tokens `TOKEN_SLOTS` already maps (plus phase-2
extensions, if any). The modal dim is part of this: `ModalScreen`'s default
dim is Textual's overlay colour, so the popup overrides dim/background to the
theme's own `background`. All four `selection-list--button*` component
classes bind theme slots. No `auto` ink, no derived washes, no tint — the same
discipline `ThemesButton` and `Live` document in their class docstrings.

---

## Phase 5 — composition-root wiring + confirm report

Goal: the popup does real I/O exactly once, through facades (spec §§7, 9).

### 5.1 `cli.py` builds, popup consumes

`cli` (composition root) constructs the provider callables
(list/read per `PROVIDERS`, dir roots from `detect`, existing-library names for skip detection) and
hands them to the editor/import state — same injection shape as
`_library()`. Concretely: extend the `run()` helper signature and
`Editor.__init__` with a providers/`ImportLibrary` param defaulting to `None`, `app.py`/`import_state` never import `themes`/`detect`
directly.

### 5.2 Confirm path

On confirm: for each planned import, `themes.create(name, slots,
source="fmt:path")`; successes and skips (`already in library`) fold into
the status line (`imported 3 themes: …`), per-theme failures and unreadable
sources fold into the session notes printed at exit (pickers' notes rule —
no modal in the popup). Truth files only: no push, no retarget, current
theme and edit buffer untouched. Empty confirm stays open with the footer
note.

---

## Appendix A — Textual facts used (docs checked 2026-10-08)

Sources: `research.md` §1 links each claim to its page.

- `SelectionList`: built for multi-toggle; items are `(prompt, value)` /
  `(prompt, value, selected)` tuples or `Selection` objects; `space`
  toggles; `selected` is the value list; `SelectedChanged` fires on any
  change (one message for bulk ops); inherits `OptionList` navigation
  (`up/down/home/end/pagedown/pageup`, `enter` selects). Per-option
  component classes for button/selected/highlighted states.
- `Collapsible`: container; `title`, `collapsed` (default `True`),
  custom `collapsed_symbol`/`expanded_symbol`; title click or focused
  `Enter` toggles; `Toggled` (with `Collapsed`/`Expanded`) messages.
  Composes via constructor children or context manager.
- Screens: `ModalScreen` dims the screen underneath and its bindings take
  precedence over app bindings; `push_screen`/`dismiss(result)` is the
  modal return path. Phase 0 spikes this against mounted takeover.
- `Button`: click or focused `Enter`; `Button.Pressed` event carries
  `event.button`; variants + `flat` for the entry-button look.
- `Static`/`Vertical`/`Horizontal`: neutral carriers for non-control rows
  (preview units mount here, not as controls).

## Appendix B — provider paths (initial)

Ghostty user dir via `detect.ghostty_themes_dir()`; shipped themes via
`GHOSTTY_RESOURCES_DIR` (default `/usr/share/ghostty`) + `/themes` —
both already exercised by `_ghostty_theme_file`. kitty/Alacritty local
dirnames pinned in tasks after a filesystem/source check; each provider
reads through its `FORMATS` reader so dialect coverage is inherited, not
re-implemented.

## Appendix C — risks

| Risk | Mitigation |
| --- | --- |
| Cross-`SelectionList` cursor feels split (candidate A) | Phase-0 spike must demonstrate the continuous walk before phase 4; else candidate B |
| Header rows toggleable/selectable (candidate B) | Skip-logic on every nav path; if any path leaks, candidate A |
| N providers × M themes heavy mount | Provider groups mount lazily per expanded state; preview reads cursor slots only, no pre-render |
| Focus trapped in popup or lost on close | Picker rule reused: list owns focus while up, grid regains it after; footer never holds it hostage |
| Popup chrome breaks I2 closure | Token-only CSS (phase 4.4); closure cases extend with the implementation |
| Name collisions across providers | Slugify + batch suffix (phase 1.3); library clashes skip with report, never overwrite |
| Second modal idiom beside the picker | Phase-0 screen spike picks one pattern; phase 4 follows it |
