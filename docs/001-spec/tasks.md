# huebox — tasks

Status: **in execution — P1 in review** · Plan: `plan.md` · Spec: `spec.md` · Rules: `AGENTS.md`

Execution order is top to bottom; phases are checkboxes, tasks are `- [ ]`.
Every phase ends with `python3 -m unittest discover -s tests` green and the
relevant spec/AGENTS/README lines updated.

Per-phase loop (orchestrator-run): implementer lands the phase → reviewer
audits the diff → fixes → suite green → one phase-commit. Delegation briefs
end with the completion ritual: agents finish their report with
`ship and done` on its own line, then call `shepherd_done` with the summary;
the orchestrator closes every agent as soon as its task is closed.

## P0 — package split (§17) — done, verify & close

- [x] `huebox/` package: color, formats/{base,ghostty,kitty,alacritty}, detect,
      render, tui, editor, cli, `__main__`, re-exporting `__init__`
- [x] `tests/` split (test_formats, test_render, test_tui, test_cli), suite green
- [x] `pyproject.toml`: `packages`, script → `huebox.cli:main`
- [x] Rewriter splice regression caught by suite and fixed (plan 0.1)
- [x] Build verified (pip/pipx unavailable in dev env; `uv build` produces a
      complete wheel: all modules + entry point)
- [x] Remove stray root `__pycache__/`
- [x] Delete `test_huebox.py`/`huebox.py` remnants from git tracking (already rm'd)

## P1 — responsive layout (§15, plan phase 1) — done, pending review

- [x] `tui.py`: `_resized` flag + `_on_winch`; `read_key` wrapped in a 0.1 s
      `select` loop returning `"resize"` on wake (plan 1.1)
- [x] `editor.py`: install the SIGWINCH handler after `enter_raw` (restored
      in `finally`); treat `"resize"` as redraw-no-op
- [x] `editor.py`: too-small screen — `MIN_COLS = 40`, `MIN_ROWS = 12` in
      `tui.py`, single centered hint via `too_small_frame()`/`clip()`
- [x] tests: resize wake (`tests/test_tui.py`), too-small render, width hold
      at 100x30/80x24/60x16/40x12/40x10, identical-frames guarantee and the
      resize no-op in the `edit()` loop (`tests/test_editor.py`)
- [x] spec: §15.4 records MIN 40x12 as a proposal (still tunable post-P5),
      §15.1 documents the mid-sequence `esc`; §8 TODO resolved, §10 grows the
      layout-sizes line

Notes: `read_key(fd, tick=RESIZE_TICK)` takes the tick so tests do not sleep a
production tenth of a second; input always wins over a pending flag, and the
flag is consumed only by the `"resize"` return. `render.py` is untouched — the
static `show` path and every width primitive are unchanged.

## P2 — staged editing + live examples (§14, plan phase 2)

- [ ] `editor.py`: `EditorState` (slots/saved/sel/undo/status/mult/armed/written)
      + `apply_key(key, st)` pure seam; `edit()` loop slims to draw/read/apply
- [ ] save semantics: Ctrl+S → write → `saved` snapshot → first-save backup;
      `r` reverts to last save; undo survives saves
- [ ] Esc: dirty → armed (`Esc again to discard`), armed → quit; other keys
      disarm; Ctrl+C same path; clean Esc quits
- [ ] `render.py`: `example_lines(slots)` — background / selection / cursor
      rows, all through clip/pack, rebuilt per frame (§14.1)
- [ ] `editor.py`: examples strip joins the extra budget above the code sample
- [ ] tests: apply_key matrix, dirty tracking, backup-once, live-everything
      (buffer change → all example rows change), frame reflects unsaved buffer
- [ ] spec: §4.3 planned-note → actual semantics; open question on autosave
      answered (no)

## P3 — theme library storage + CLI (§13.1–13.5, plan phase 3)

- [ ] `themes.py`: home/themes_dir/state_path, name validation, `create`,
      `load` (gap-tolerant → MISSING + warnings), `list_themes`, `current`,
      `set_current`; lazy mkdir
- [ ] `themes.py`: canonical theme writer ([theme]/[colors], created preserved,
      modified bumped, source recorded) + atomic tmp/replace write
- [ ] `themes.py`: hand-rolled reader for our TOML subset (§3.2), unknown-key
      counting
- [ ] `themes.py`: `RAMP` fallback palette (plan 3.3, tune before merge)
- [ ] `cli.py`: actions new/list/use/import + `name` positional; `--force`,
      `--from`; `edit`/`show`/`--dump` accept a theme name; legacy fallback
      rules (§13.4)
- [ ] `editor.py`: theme-mode write callback (truth file) vs legacy writer
- [ ] AGENTS.md: themes.py row + dependency rule update
- [ ] tests: names, round-trip, gaps/warnings, unknown keys, timestamps,
      create-refusal, list marking, state robustness, new/import seeding,
      dump-from-theme
- [ ] spec: close open questions 3 (ramp values) and 5 (rm/mv enough for now)

## P4 — push on save + use (§13.6, plan phase 4)

- [ ] `themes.py`: `push(fmt, slots, to=None, no_push)` — resolve targets,
      refuse colourless configs, line-level write, missing-key report,
      truth-first/never-rollback
- [ ] `cli.py`: build the save callback from `--to`/`--no-push`; `use` =
      set_current + push (exit 1 on push failure); report lines to stderr
- [ ] `editor.py`: status `saved <theme> → <fmt>` on save
- [ ] tests: push fixtures (kitty/ghostty), missing-key report, --no-push
      no-op bytes, colourless-target failure after truth write, use exit codes
- [ ] spec: §6.2 planned note → save pipeline; decision log entry for
      report-only push (open question 1 stays open)
- [ ] README: push-on-save section

## P5 — TUI picker + save-as-new (§13.7, plan phase 5)

- [ ] `editor.py`: `t` overlay (list/pack rows, arrows, Enter opens + sets
      current, `n` new-from-buffer via name prompt, Esc back)
- [ ] dirty-switch block with `save (Ctrl+S) or revert (r) first`
- [ ] `N` save-as-new (name prompt, existing-name confirm) — the legacy
      migration path
- [ ] status bar: `theme ● fmt` / `direct:<path>` + dirty dot
- [ ] tune MIN sizes (P1 TODO closes); overlay under too-small check
- [ ] tests: overlay apply_key flows, dirty block, save-as-new, overlay
      rendering within width budget
- [ ] spec: close §4.3 raw-mode TODO (one enter/exit pair rule, prompt paths
      enumerated); AGENTS.md guideline if patterns changed

## P6 — Ghostty native export (§13.6 phase 2, plan phase 6)

- [ ] `themes.py`: `export_ghostty_native(name, slots)` →
      `~/.config/ghostty/themes/<name>` via write_flat + GHOSTTY_RULES template
- [ ] `detect.py`: `ensure_theme_pointer(config_path, name)` — rewrite or
      append `theme = name`, line-level
- [ ] `cli.py`: `--ghostty-native` opt-in flag wiring (default decided here:
      plan proposes opt-in)
- [ ] tests: exported round-trip, pointer rewrite byte-exact except theme
      line, append case, kitty/alacritty unaffected
- [ ] spec: close open question 2 with the decision; decision log entry

## P7 — hygiene + release (plan phase 7)

- [ ] `detect.py`: `KITTY_CONFIG_DIRECTORY` (old spelling secondary);
      verify/drop `ALACRITTY_CONFIG_DIR`/`ALACRITTY_CONFIG`
- [ ] Alacritty search order gains `$XDG_CONFIG_HOME/alacritty.toml`
- [ ] `with open(...)` sweep — silence ResourceWarnings (zero-behaviour)
- [ ] README/AGENTS/spec full sync; version → 2.0.0; changelog blurb
- [ ] final: full suite + `pipx install .` smoke + one manual editor session
      in ghostty (resize, save, picker, push)
