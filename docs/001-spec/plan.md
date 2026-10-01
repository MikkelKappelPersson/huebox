# huebox — implementation plan

Status: **ready for review** · Plans spec: `spec.md` §§1–17 · Conventions: `AGENTS.md` · Next: `tasks.md`

This turns the spec into build order. The spec says *what*; this says *how*,
module by module, phase by phase. Terminal facts in Appendix A were verified
against upstream docs in October 2026 (sources linked). Everything here is
behind `python3 -m unittest discover -s tests` staying green.

| Phase | Delivers | Spec | Touches |
| --- | --- | --- | --- |
| 0 | Package split (done) | §17 | repo layout |
| 1 | Responsive layout | §15 | `tui.py`, `editor.py`, `render.py` |
| 2 | Staged editing + live examples | §14 | `editor.py`, `render.py` |
| 3 | Theme library storage + CLI | §13.1–13.5 | `themes.py`, `cli.py`, `editor.py` |
| 4 | Push on save + use | §13.6 | `themes.py`, `cli.py`, `editor.py` |
| 5 | TUI picker + save-as-new | §13.7 | `editor.py` |
| 6 | Ghostty native theme export | §13.6 (phase 2) | `themes.py`, `detect.py` |
| 7 | Hygiene: env vars, fd leaks, docs sync | §7, §10 | `detect.py`, `formats/` |

---

## Phase 0 — package split (landed, pending review)

`huebox.py` (905 lines) → package per §17: `color.py`, `formats/` (base +
ghostty + kitty + alacritty + registry), `detect.py`, `render.py`, `tui.py`,
`editor.py`, `cli.py`, `__main__.py`; `__init__.py` re-exports the v1 public
names. `test_huebox.py` → `tests/` (test_formats, test_render, test_tui,
test_cli). `pyproject.toml` → `packages` + `huebox = "huebox.cli:main"`.

Residuals for review:

1. A rewriter splice bug was introduced and fixed during the split — the
   round-trip suite caught it, which is the §10 contract working as intended.
2. Confirm `pipx install .` builds (console script, packages declared).
3. Delete stray root `__pycache__/`.

## Phase 1 — responsive layout (§15)

Goal: the editor follows terminal resizes without keypresses, and degrades to
a legible too-small screen instead of garbling.

### 1.1 Wake on SIGWINCH

`tui.py` gains a module-level flag and the editor installs the handler:

```python
_resized = False
def _on_winch(signum, frame): global _resized; _resized = True
```

`edit()` calls `signal.signal(signal.SIGWINCH, _on_winch)` after `enter_raw`.
`tui.read_key()` keeps its byte-at-a-time escape parsing but is wrapped in a
`select` loop:

```python
while True:
    ready, _, _ = select.select([fd], [], [], 0.1)
    if ready:
        break                      # fall through to existing parser
    if _resized:
        return "resize"            # editor redraws, no key consumed
```

The editor loop treats `"resize"` as a no-op key: the `while True` continues
and `draw_editor` re-queries `term_size()`. Esc parsing is unchanged; a
SIGWINCH landing mid-sequence raises the flag but does not break the
sequence — PEP 475 auto-retries the interrupted `os.read`, so parsing
continues and the redraw is owed at the next idle tick.

### 1.2 Too-small screen

`draw_editor` head: if `cols < MIN_COLS or rows < MIN_ROWS`, render a single
centered line `terminal too small — need {MIN_COLS}x{MIN_ROWS}` (31 columns,
so the hint survives unclipped at any width above 30) on a blank screen and
return. Proposal: `MIN_COLS = 40`, `MIN_ROWS = 12`
(spec leaves the exact number open; tune after the picker lands in Phase 5).
`render_preview` (static) is untouched — it already clips.

### 1.3 Tests

- `read_key` returns `"resize"` when the flag is set and no byte arrives:
  drive `select` via a patched fd that is always not-ready, set `_resized`,
  assert return within one 0.1 s tick (patch the timeout for speed).
- `draw_editor(too_small size)` output contains the enlarge hint and no
  swatch lines; at 80x24 output is unchanged.
- Byte-identical frame guarantee: two identical `draw_editor` calls with the
  same args produce the same string.

## Phase 2 — staged editing + live examples (§14)

Goal: keystrokes mutate a buffer, disk changes only on save, and every
preview element (background, selection, cursor, text, grid, code sample)
re-renders from the buffer on the same frame.

### 2.1 Pure key-handling seam

Extract the key switch from `edit()` into `apply_key(key, st) -> None`, where
`st` is a plain class holding everything the loop mutates:

```python
class EditorState:
    slots: dict          # the buffer (rendered, not saved)
    saved: dict          # last-save snapshot — dirty = slots != saved
    sel, undo, status, mult, armed   # armed = double-Esc pending
    written: bool        # any disk write this session
```

`edit()` becomes: enter raw → `while True: draw; key = read_key;
if key == "resize": continue; if key is quit and safe: break;
apply_key(key, st)`. The write callback is passed in and stored on `st`;
**only** `apply_key`'s Ctrl+S branch calls it. `i`/`X` hex prompting stays in
`edit()` (it needs raw-mode exit; it is re-injected as a callback
`prompt_hex(name)` so `apply_key` stays pure and testable).

Key semantics (spec §14.2):

- `Ctrl+S`: `write(st)` → `saved = dict(slots)`; first write of the session
  creates `<path>.huebox.bak` if absent (rule moved from editor-open to
  first-save, per spec); status `saved`.
- Esc: if `dirty()` → arm (`unsaved changes — Esc again to discard`); if
  already armed → quit. Any other key disarms. Clean Esc quits immediately.
  `Ctrl+C` follows the same path.
- `r`: `slots = dict(saved)` (was session start) + `undo.clear()`.
- `u` unchanged; still works across saves (undo log not cleared by save).

### 2.2 Examples strip (§14.1)

`render.py` gains `example_lines(slots) -> list[str]` — pure, like
`sample_lines`. Three rows, each a label + a live swatch:

- `background`: `bg(background)` fill with `fg(foreground)` text — the
  quick-brown-fox line, hex shown in DIM.
- `selection`: `bg(selection-background)` with `fg(selection-foreground)`
  text: `selected text`.
- `cursor`: `bg(cursor-color)` block `██` + `fg(cursor-text)` sample char,
  hex label.

All through `clip`/`pack`, hex values pulled from the buffer per frame.
`draw_editor` inserts them in the existing `extra` budget (examples above the
code sample; both shrink together as rows shrink). No cached colours: the
strip is rebuilt from `st.slots` every frame like everything else.

### 2.3 Tests

- Drive `apply_key` directly: adjust → dirty, no `write` called; Ctrl+S →
  write called once, clean; Esc dirty → armed; Esc armed → quits;
  second-Esc disarm on other key; `r` reverts to `saved` not session start.
- First save creates the backup, later saves don't overwrite it.
- `example_lines` output contains the current buffer's hex strings; changing
  one slot changes all three rows' escapes on the next call (the
  live-everything assertion).
- `draw_editor` reflects an unsaved buffer mutation (frame = buffer, not
  disk).

## Phase 3 — theme library storage + CLI (§13.1–13.5)

Goal: `~/.config/huebox/` is the truth. New module `themes.py` (spec §13).
AGENTS.md gains its row (spec sections §13, imports: `color` + `formats`).

### 3.1 `themes.py` API

```python
home() -> str                     # $XDG_CONFIG_HOME/huebox | ~/.config/huebox
themes_dir() -> str               # home()/themes
state_path() -> str               # home()/state.toml
valid_name(name) -> bool          # ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$
current() -> str | None           # state.toml: current = "name"
set_current(name) -> None
create(name, slots, source=None, force=False) -> path   # refuses existing
load(name, warnings=None) -> dict                       # 22 slots, gaps -> MISSING
list_themes() -> list[tuple[str, str, float]]           # name, path, mtime
```

Creation is lazy (`create` mkdirs). `state.toml` is two lines max
(`current = "…"`) written by hand (no tomllib, 3.9); a missing or corrupt
state file reads as `None` and is repaired on the next `set_current`.

### 3.2 Theme file grammar (our own TOML subset)

Reader: strip comments/blank lines; `[section]` headers; `key = "value"`
quoted strings only; hex validated with `is_hex`. Unknown keys are ignored
but counted → `warnings.append("unknown keys dropped")`. Missing slots load
as `MISSING` grey + warning (spec §13.2). Writer emits the canonical layout
from spec §13.2 byte-for-byte: `[theme]` header block (name/created/modified/
source — `created` preserved from the existing file, `modified` = now, local
ISO-8601 seconds) then `[colors]` with all 22 slots, palette-then-named.

Atomicity: truth files are written to `path.tmp` then `os.replace`d. Terminal
configs keep the in-place line-level writer (their contract forbids
rewrites); the atomic write only applies to files huebox owns.

### 3.3 Fallback ramp (spec open question 3)

`new` with no colours to seed from uses `RAMP` in `themes.py` — proposal,
tune before merge: bg `#101014`, fg `#e6e6ea`, cursor `#e6e6ea` /
`#101014`, selection `#2a2a34` / `#e6e6ea`, palette 0–7
`#101014 #a83232 #3f7a3f #a88a3f #3f6a8a #8a3f6a #3f8a8a #b0b0b8`,
8–15 brighter siblings (`#d0d0d8`, `#e06c6c #6cc06c #e0c06c #6c9ce0 #e06c9c #6cc0c0 #f0f0f8` style).

### 3.4 CLI

`action` positional grows `new`, `list`, `use`, `import`; a second positional
`name` (`nargs="?"`) serves `new/import/use/edit/show/dump`. Per-command
validation (name required for new/use/import, invalid → `huebox: invalid
theme name`, exit 1). Flags added: `--force` (import overwrite),
`--from <fmt>` (import source; Phase 4 also uses it).

| Command | Phase-3 behaviour |
| --- | --- |
| `new <name>` | validate, seed from detected terminal (or `RAMP`), `create`, `set_current`, open the theme in the editor |
| `import <name>` | `resolve()` → read → `create` (refuses without `--force`); does **not** change current |
| `list` | one theme per line: `name  source-fmt  modified`; current marked `*` |
| `edit [name]` | name → theme; no name → `current()`; no current & no themes → legacy direct mode (§13.4); state pointing at a deleted file → warn + legacy mode |
| `show [name]` / `--dump [name]` | read from `themes_dir/<name>.toml` instead of a terminal config |

The editor in theme mode gets `write=save-theme-callback`; in legacy mode it
keeps `FORMATS[fmt]["write"]`. `N` (save-as-new) lands with the picker in
Phase 5; until then, new themes come from `new`/`import` only.

### 3.5 Tests

Name validation (valid, 64-char, bad chars, empty); create → load
round-trip all 22 slots; gap-tolerant load fills `MISSING` + warns; unknown
key warning; `created` preserved / `modified` bumped on re-save; create
refuses existing without `--force`; `list` marks current; state.toml
missing/corrupt/deleted-theme paths; `new` seeds from a fixture config and
falls back to `RAMP`; `--dump <name>` output shape; `import` snapshot
equality with the source config's slots.

## Phase 4 — push on save + use (§13.6)

Goal: saving the truth pushes to the terminal you're in. No separate apply.

### 4.1 `themes.py` push

```python
push(fmt, slots, to=None, no_push=False) -> list[str]  # report lines
```

- Targets: `to` (comma list) else `[fmt]` from today's `resolve()`.
- Per target: `resolve()` (refuses targets whose config has no colours —
  same rule as detection, stderr + failure) → `FORMATS[fmt]["write"]` —
  reusing the line-level writers keeps the §6.2 contract byte-for-byte.
- Missing-key report: read the target first; `SLOTS - target.keys()` →
  `not carried by this config: …`. Push never inserts keys (open question 1
  stays open; report-only is the default).
- Report lines go to stderr when any target fails; exit 1 after truth is
  written (truth-first is decision 7 — a failed push never rolls back).

### 4.2 Save pipeline

`cli.py` builds one `save` callback from flags and hands it to `edit()`:

```
save(slots): save_theme(current, slots)   # truth, atomic
             if not no_push: push(...)    # terminal(s)
             -> status string: "saved ember → ghostty"
```

Legacy direct mode: save = `FORMATS[fmt]["write"]` exactly as today.
`use <name>` = `set_current` + push (exit 1 on push failure); it takes no
editor. `--no-push` writes truth only.

### 4.3 Tests

push into a kitty/ghostty fixture (values land, neighbours untouched);
missing-key report text; `--no-push` leaves the config's mtime/bytes alone;
push to a config without colours fails after truth was written; `use`
exit codes; status string shape.

## Phase 5 — TUI picker + save-as-new (§13.7)

- `t` opens the theme overlay: rows from `list_themes()` via `pack`, current
  marked; arrows + Enter → load into the buffer (`load` + reset
  `saved`/`undo`/`sel`), `set_current`, status `opened ember`; `n` → name
  prompt (same raw-exit trick as hex entry) → create from the current buffer
  → set current; Esc back. Switching while dirty is blocked with
  `save (Ctrl+S) or revert (r) first` (decision 12).
- `N` = save the buffer as a new theme (name prompt, `--force`-like confirm
  if the name exists) — the migration path out of legacy sessions (§13.4).
- Status bar gains the `theme ● fmt` / `direct:<path>` form and the dirty
  dot `●` (buffer ≠ saved).
- Overlay layout goes through `pack`/`clip`; it participates in the
  too-small check; minimum sizes tuned here (Phase-1 TODO closes).
- Tests: `apply_key`-level (open overlay, switch, dirty-block, save-as-new
  with a mocked prompt) + `draw_editor` renders overlay rows under width
  budget.

## Phase 6 — Ghostty native export (§13.6 phase 2)

Behind an opt-in flag (open question 2 leans opt-in; default decided at
review): `--ghostty-native`.

- `themes.py`: `export_ghostty_native(name, slots) -> path` writes
  `~/.config/ghostty/themes/<name>` using `write_flat` + `GHOSTTY_RULES`
  over a canonical template (all 22 lines, `palette = 0=#…` + named) — same
  dialect, same writer, round-trip-safe by construction.
- `detect.py`: `ensure_theme_pointer(config_path, name)` rewrites the
  `theme =` line in the main Ghostty config (value swap, comments intact) or
  appends `theme = name` if absent. Other theme files untouched.
- Push to Ghostty with the flag: export + pointer, instead of the inline
  phase-1 path. kitty/Alacritty unaffected.
- Tests: exported file round-trips via `read_flat`; pointer rewrite
  preserves the config byte-for-byte except the theme line; append case;
  shipped-theme copies no longer needed for push (existing `_ghostty_theme_file`
  copy path stays for legacy inline mode).

## Phase 7 — hygiene (continuous, batched last)

1. `detect.py` env-var fixes from Appendix A: `KITTY_CONFIG_DIR` →
   `KITTY_CONFIG_DIRECTORY` (keep the old spelling as a secondary probe for
   one release); verify/drop `ALACRITTY_CONFIG_DIR` / `ALACRITTY_CONFIG`
   (upstream documents none — likely delete).
2. Alacritty search order gains `$XDG_CONFIG_HOME/alacritty.toml`
   (upstream list in Appendix A); `~/.alacritty.yml` stays for legacy.
3. `with open(...)` everywhere — kills the ResourceWarnings the suite emits
   today (pre-existing in v1, zero-behaviour change).
4. Docs sync: README (theme commands + new save model), AGENTS.md (themes.py
   row, `use`/`import` notes), spec TODOs closed with decisions.
5. Version: theme library + staged saving is the 2.0.0 line (save semantics
   change; `edit` without themes keeps working, so not a hard break).

---

## Appendix A — verified terminal facts (sources checked 2026-10)

**kitty** (sw.kovidgoyal.net/kitty/conf, manpages.debian.org kitty.conf(5)):
config search `$XDG_CONFIG_HOME/kitty/kitty.conf` → `~/.config/kitty/kitty.conf`;
`KITTY_CONFIG_DIRECTORY` is the env override (**not** `KITTY_CONFIG_DIR`);
`include other.conf` is relative to the current file, env-expanded;
`globinclude` / `envinclude` exist; reload = `ctrl+shift+f5`
(`load_config_file`).

**Ghostty** (ghostty.org/docs/config/reference): theme files are full Ghostty
configs — same syntax; user themes live in `$XDG_CONFIG_HOME/ghostty/themes`
(= `~/.config/ghostty/themes`), shipped themes in the resources `themes/`
dir (`ghostty +list-themes`); `theme = Name` selects one; `config-file =
?path` is the optional-include form huebox already parses; reload =
`Ctrl/Cmd+Shift+,` (`reload_config`).

**Alacritty** (alacritty.org/config-alacritty.html, mankier.com/5/alacritty):
search order `$XDG_CONFIG_HOME/alacritty/alacritty.toml`,
`$XDG_CONFIG_HOME/alacritty.toml`, `~/.config/alacritty/alacritty.toml`,
`~/.alacritty.toml`, `/etc/…`; colours are `#RRGGBB` with `#` under
`[colors.primary]` / `[colors.normal]` / `[colors.cursor]` /
`[colors.selection]`; `general.import = [...]` merges in order (last wins),
paths absolute, `~/`-prefixed or relative to the importing file; no env var
for the config path is documented.

Implications already folded into phases: kitty env fix (P7.1), Alacritty
import as a possible push path is **not** needed for v2 — push targets
whatever file currently holds colours, which `resolve()` already finds; the
`import` directive only matters if we ever generate configs (out of scope,
§3).

## Appendix B — data formats

`state.toml`:

```toml
current = "ember"
```

Theme file: the §13.2 example is the canonical writer output. Reader accepts
any key order, quoted or bare hex strings (bare stored values are normalised
on load), extra whitespace, and comments anywhere. Names: file name =
theme name = `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, case-sensitive; `create`
checks case-insensitively for collisions too (case-insensitive filesystems).

## Appendix C — risks

| Risk | Mitigation |
| --- | --- |
| SIGWINCH mid-escape-sequence | Does not break the sequence — PEP 475 auto-retries `os.read`; redraw owed at next idle tick (§15.1) |
| Hand-rolled TOML vs. exotic user files | Theme files are huebox-owned with a fixed grammar; loader warns + drops unknowns instead of failing |
| Stale `GHOSTTY_RESOURCES_DIR` pushing to the wrong machine's paths | Push resolves the target config and refuses if it has no colours (P4) |
| Raw-mode exit paths multiply (prompt, overlay, save-as-new) | One `enter/exit_raw` pair per raw session, re-enter after prompts; §4.3 TODO stays open until P5 closes it |
| Case-insensitive FS name collisions | `create` checks case-insensitively (B) |
| Rewrite drift (HSV round-trip) | Spec TODO stays open; not a v2 blocker, edits accumulate hex-to-hex |
