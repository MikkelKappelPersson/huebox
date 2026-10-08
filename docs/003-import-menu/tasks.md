# huebox 003 — tasks

Status: **approved** · Plan: `plan.md` · Spec: `spec.md` · Rules: `AGENTS.md`

Execution order is top to bottom; phases are checkboxes, tasks are `- [ ]`.
Every phase ends with `python3 -m unittest discover -s tests` green and the
relevant spec/plan lines updated. New tests stay minimal — only the
absolutely critical paths get cases (name mapping, confirm planning,
provider failure tolerance); spec §10's full harness stays deferred.
Phases otherwise guard only against regressions in the existing suite.

Per-phase loop (orchestrator-run): implementer lands the phase → reviewer
audits the diff → fixes → suite green → one phase-commit. Delegation briefs
end with the completion ritual: agents finish their report with
`ship and done` on its own line, then call `shepherd_done` with the summary;
the orchestrator closes every agent as soon as its task is closed.

## P0 — Textual spike (plan phase 0) — throwaway, decides spec §8.2

Spike lives outside `huebox/` (or deleted after); nothing here ships.

- [ ] Spike app: three `Collapsible`s (fake Ghostty/kitty/Alacritty, ~30 rows
      each) × one `SelectionList` each (candidate A), no `Footer`, key surface
      limited to arrows/space/enter (research.md §2 script as starter)
- [ ] Candidate A proven: programmatic `highlighted` hand-off across lists,
      single focus moving with cursor, merged `SelectedChanged` footer count,
      collapsed groups skipped never deselected
- [ ] Candidate B trial: one `SelectionList` with disabled header rows; note
      every nav path that must skip headers (`up/down/home/end/pagedown/pageup/enter`)
- [ ] Screen pattern trial: `ModalScreen` (`push_screen`/`dismiss`) vs mounted
      takeover inside `Editor.redraw`; judge focus ownership, `Esc`/`Ctrl+C`
      path, resize/redraw, picker code path untouched
- [ ] `Enter`-hijack proven: confirm-the-set fires once, toggle never double-fires
      (screen-binding precedence or `BINDINGS` override — record which)
- [ ] Wheel verdict: custom `on_wheel` moving highlight vs native viewport scroll
      (spec: cursor-follows-wheel)
- [ ] `I` proven free in `apply_key` + grid bindings (lowercase `i` is hex entry)
- [ ] Verdict note appended to `research.md`: structure, cursor/focus ownership,
      `Enter` mechanism, wheel, `I`, message flow, screen pattern + rejected
      reason in one line

## P1 — provider registry (plan phase 1, spec §§7–8.1)

- [ ] New `providers.py`: `PROVIDERS` registry mirroring `FORMATS`
      (`["list"]` → files, `["read"]` → slots via `FORMATS[fmt]["read"]`);
      imports `color` + `formats` only, never `themes`/`detect`
- [ ] Dir roots arrive injected from `cli` (Ghostty user dir via
      `detect.ghostty_themes_dir()`, shipped via `GHOSTTY_RESOURCES_DIR` +
      `/themes`); pin kitty/Alacritty local dirnames after filesystem check
- [ ] Discovery: non-recursive, suffix rules per provider (Ghostty:
extensionless `themes/<name>` files list; kitty: `.conf`; Alacritty:
`.toml`/`.yml`); missing/unreadable dir →
      empty group, never an exception; zero-colour files never list (skipped
      per-file with a note)
- [ ] `slugify()` pure: lowercase, non-`[a-z0-9]` runs → `-`, strip leading `-`,
      stem truncated for `-N` room within 64; batch `-2`/`-3`, library clashes
      reported upstream not resolved here; case-insensitive like `create`
- [ ] Suite green, no behaviour change outside the new module

## P2 — shared preview units (plan phase 2, spec §6)

- [ ] Hoist `example_lines`, `sample_lines`, swatch-cell rendering
      (`swatch_cell`/`named_cell` family) into importable pure units (slots in,
      ANSI rows out); define the compact 16-slot palette form once
- [ ] Editor frame calls the same units with byte-identical output (existing
      suite is the guard); popup will call them at its own width
- [ ] No widget imports inside the units; new `TOKEN_SLOTS` entries, if any,
      map theme slots only (I2-structural)
- [ ] Suite green; spec `§6`/`001` guideline note if the shared-module rule moved anything

## P3 — headless import state (plan phase 3, spec §§4.3–4.4, §9)

- [ ] New state module (name TBD): provider order + theme ids, expanded set,
      cursor over open groups only, toggled id set, footer note, read-once slots
      cache (injected reader only on first highlight)
- [ ] Pure transitions: `up/down` across open groups (collapsed skipped),
      `left/right` collapse/expand under cursor, `space` toggle, `a` all-visible
      (open groups only) / `n` clear; editor colour keys have no branch (inert
      by construction)
- [ ] Click/wheel resolve through the same transitions (move-cursor-then-toggle)
- [ ] Interlock: takeovers never stack — `t`/picker keys have no branch while
      import state is up, `I` has none while the picker is up
- [ ] Confirm mapping: toggled set → slugified unique names + skip-list
      (library clashes → skip entries) + per-theme failure notes; returns a plain
      plan, writes nothing
- [ ] Abandon (`Esc`/`I`/`Ctrl+C`, empty confirm stays open with footer note);
      state imports neither `themes` nor `detect` — listing/reading injected
      like the picker's `Library`
- [ ] Suite green (new module adds no Textual import)

## P4 — popup shell in `app.py` (plan phase 4, spec §§4–5)

Follows the P0 verdict without re-litigating; one screen pattern only.

- [ ] `Import` button under `Themes`: same flat contract (unfocusable,
      selection-pair paint, underline hover), same blank-fill row discipline
      (no new row); `HUEBOX_EDITOR_PANEL=0` keeps no button, `I` still works
- [ ] Takeover + layout: left list column (P0 structure), right preview column
      (P2 units in `Static`/`Vertical`), footer (hints + live count +
      confirm/cancel `Button`s); `MIN_COLS`/`MIN_ROWS` reuse, same too-small line;
      preview sheds sample-tail-first, strip keeps floor (spec §4.5)
- [ ] Focus/keys/messages: popup owns focus while up; exactly one list focused,
      focus-follows-cursor; footer via screen bindings + click (no tab trap);
      close returns focus to the colour-selection grid; `on_key` routes to the
      P3 surface; `SelectedChanged` → count, cursor move → preview repaint,
      `Collapsible.Toggled` → expanded-set persistence (`Live._collapsed` pattern);
      `a` loops `select_all` over open groups, collapsed explicitly skipped
- [ ] Preview read-only: preview carriers mount with selection off
      (`ALLOW_SELECT = False` discipline) — no text selection in the popup (spec §4.5)
- [ ] Theme-closure: token-only CSS, modal dim/background overridden to theme
      `background`, all four `selection-list--button*` classes bound to theme slots
- [ ] Suite green; no picker-path behaviour change

## P5 — composition-root wiring + confirm report (plan phase 5, spec §7)

- [ ] Read the seam first: confirm the actual `run()` helper +
      `Editor.__init__` signature in `cli.py`/`app.py`, then extend with a
      providers/`ImportLibrary` param defaulting to `None`
- [ ] `cli.py` builds provider callables (list/read, dir roots from `detect`,
      existing-library names for skip detection) and hands them down;
      `app.py`/state never import `themes`/`detect`
- [ ] `I` key + `import-button` through one call (mirrors `Themes`/`t` rule);
      opening never blocked by dirty buffer, close/confirm never touches buffer
- [ ] Confirm path: `themes.create(name, slots, source="fmt:path")` per planned
      import; status line (`imported N themes: …` / skips named); per-theme
      failures to session notes printed at exit (picker-notes rule, no in-popup
      modal); truth files only — no push, no retarget, current + buffer untouched
- [ ] Empty confirm stays open with footer note; bare-stack `I` path works
- [ ] Suite green; spec §§7–8/11 + README/AGENTS/`code-guidelines.md` lines
      updated — including the Architecture module table + dependency rule for
every new module (providers, state, preview units), no exceptions
- [ ] Minimal critical tests only: `slugify` (runs → `-`, leading-dash strip,
      64 cap with `-N` room, batch `-2`/`-3`, case-insensitive library comparison),
      confirm mapping (toggled set → names + skip-list, writes nothing,
      truth-only), provider missing/unreadable dir → empty group with no
      exception. Everything else in spec §10 stays deferred.
