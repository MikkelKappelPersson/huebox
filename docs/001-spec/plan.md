# huebox — implementation plan

Status: **all seven phases landed** · Plans spec: `spec.md` §§1–17 · Conventions: `AGENTS.md` · Next: `tasks.md`

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
| 7 | Hygiene: env vars, fd leaks, no-op writes, docs sync | §7, §10 | `detect.py`, `formats/`, docs |

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

As built (P4 review settled the shape — the plan's original `fmt`-first
signature returning a bare list forced text-sniffing for the verdict):

```python
push(slots, to=None, fmt=None, path=None, no_push=False)
    -> PushResult(lines, pushed, failed)
```

- Targets: `to` (comma list) else `[fmt]` from today's `resolve()`; `path`
  is the explicit `--config` seam and pushes exactly one target — an
  explicit path with more than one target name raises `ValueError`.
- Per target: `resolve()` (refuses targets whose config has no colours —
  same rule as detection, stderr + failure) → `FORMATS[fmt]["write"]` —
  reusing the line-level writers keeps the §6.2 contract byte-for-byte.
- Missing-key report: read the target first; `SLOTS - target.keys()` →
  `not carried by this config: …`. Push never inserts keys (open question 1
  stays open; report-only is decision 18).
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
editor. `--no-push` writes truth only. `--config` plus a single `--to`
names one file for one format; `--config` plus several `--to` targets is
refused before any write (ambiguity, not a guess).

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

As built: the picker **replaces** the frame while it is open (one frame, one
budget — decision 19), so it needed no wider floor than `MIN_COLS = 40` /
`MIN_ROWS = 12`, which §15.4 keeps. The library reaches `editor.py` as an
injected `Library` (list / load / create), so the dependency rule still holds,
and the save callback became `write(theme, path, values)` — the name travels
with every save because a switch has to retarget `Ctrl+S` mid-session. `n` and
`N` both adopt the new theme as the session's subject (and set it current);
`N` then runs the ordinary save pipeline, so one writer and one push contract
cover every theme the editor ever writes. Loading a theme also resets the arm,
the undo log and the selection (§13.7, P2 review).

## Phase 6 — Ghostty native export (§13.6)

The export path itself is unchanged from the plan: `--ghostty-native` and
everything under it. What changed after release is **which path a save takes
by default** (decision 26, superseding decision 22): the export is the
default for a config that is organised by theme, `--ghostty-native` now
forces it where the colours are inline, and `--ghostty-in-place` forces the
edit. The plan's reasoning for opt-in was that phase 1 "only edits the file
that already holds the colours", and that was the load-bearing claim to
break: with `theme = Nightspice` in the config, the file that holds the
colours *is* Nightspice's, so saving a theme called `test` re-badged it in
place. One `theme =` swap is a visible, reversible, reported line; a silent
re-badging of somebody else's theme file is not.

As built (decision 22: **opt-in**, phase 1 unchanged as the default):

- `themes.py`: `export_ghostty_native(name, slots) -> path` writes
  `$XDG_CONFIG_HOME/ghostty/themes/<name>` via write_flat + GHOSTTY_RULES
  over a canonical template (all 22 lines, `palette = 0=#…` + named) — same
  dialect, same writer, round-trip-safe by construction. The template goes
  to a sibling tmp, `FORMATS["ghostty"]["write"]` splices the values in,
  `os.replace` finishes it: one rename, never a half theme. The name goes
  through `valid_name` (it is a file name in a directory we do not own) and
  a gap slot is written as the same `MISSING` grey a push sends.
- `detect.py`: `ensure_theme_pointer(config_path, name)` rewrites the
  `theme =` line in the main Ghostty config (value swap, spacing, quotes
  and trailing comment intact) or appends `theme = name` if absent; a config
  already pointing at `name` is not rewritten at all, so a no-op save keeps
  its mtime. Other theme files untouched. Returns
  `unchanged` / `rewritten` / `appended` for the report.
- Two seams the plan did not name, both forced by §7: **`ghostty_main_config()`**
  — the pointer goes in the file that *holds* `theme =`, not the one
  `resolve()` follows to the colours — and **`config_holds_colours()`**, the
  §7.3 "has colours" question asked of a *chain*, so a main config that
  holds nothing but a pointer still counts as a terminal while a dangling
  pointer still does not.
- `_ghostty_theme_file` now reads the value with the same `THEME_LINE`
  regex the writer uses. A trailing comment on `theme = ember  # mine` used
  to make the reader look for a file called `ember"   # mine`, which is why
  the reader and the writer had to be pinned by one test.
- The writer opens with `newline=""` on both ends: universal newlines would
  have rewritten a CRLF config, and `surrogateescape` carries a non-UTF-8 one
  through byte for byte.
- Push to Ghostty with the flag: export + pointer, instead of the inline
  phase-1 path. kitty/Alacritty unaffected (a `--to ghostty,kitty` run
  exports for ghostty and pushes kitty the ordinary way).
- `push()` grew `ghostty_native` and `name`; `PushSpec` grew
  `ghostty_native` (default `False`, so the existing four-argument
  constructions in the suite still work). `--ghostty-native --no-push` is
  refused before anything is written, and a legacy direct session has no
  name to export under — it writes the config and says so in the
  post-session `notes` list, never inside the frame. (Both of those changed
  with decision 26: `--no-push` now simply wins, and the refusal to export
  without a name became an in-place write, which is what such a session
  always did.)
- Tests: exported file round-trips via `read_flat`; pointer rewrite
  preserves the config byte-for-byte except the theme line; append case
  (config without `theme =`, with and without a trailing newline);
  kitty/Alacritty unaffected; illegal name; CLI `use`/`edit` end to end;
  shipped-theme copies no longer needed for push (existing
  `_ghostty_theme_file` copy path stays for legacy inline mode).

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
5. Version: theme library + staged saving is the 0.2.0 line (save semantics
   change; `edit` without themes keeps working, so not a hard break).

As built (P7):

- **Env probes** are the format table's `env` dict, probed in insertion order,
  so the right spelling goes first and nothing else had to move:
  `KITTY_CONFIG_DIRECTORY`, then `KITTY_CONFIG_DIR` marked deprecated in a
  comment (dropped with the next minor bump); alacritty's two invented vars
  are gone, because upstream documents none (Appendix A) and an override named
  after a variable the terminal does not read is a wrong answer (decision 23).
  The removal is user-visible, so it is stated in the spec, the README and the
  2.0 blurb rather than done quietly.
- **Alacritty's list** is upstream's search order with the legacy stops kept:
  `$XDG_CONFIG_HOME/alacritty/alacritty.toml`, `~/.config/alacritty/alacritty.toml`,
  `$XDG_CONFIG_HOME/alacritty.toml`, `~/.config/alacritty.toml`, then huebox's
  own `alacritty.yml` and upstream's `~/.alacritty.toml`. The `~/.config/`
  spellings are already rewritten to `$XDG_CONFIG_HOME` at import, so one more
  entry was the whole change.
- **The fd sweep** was six `open()` calls, not two: both flat readers and
  writers in `formats/base.py`, `formats/alacritty.py` (`toml_entries` is both
  a public generator and the writer's own position source) and `detect.py`'s
  two Ghostty readers — plus three in `tests/test_formats.py`, which had been
  emitting warnings of their own. 119 ResourceWarnings in the suite before, 0
  after, no behaviour change.
- **No-op writes** no longer open the file (decision 24): both writers build
  the new line list, compare, and return before a write handle exists, so
  §6.2 rule 4 is a property of the writer rather than of the push path above
  it. The byte-identity test grew an mtime assertion and a push of a config's
  own colours gained one too; both fail against the v1 writers.
- **`_ghostty_includes`** reads `config-file = path  # comment` the way the P6
  fix taught `theme =` to be read — quoted or bare value, bare stops at the
  first `#`, spacing preserved, `?path` still relative to the config. Same bug
  class, same shape of fix, one rule per key instead of a lenient regex.
- **Docs**: spec TODOs carry their answers as `>` notes (§5.1 rounding and the
  140 threshold, §6.2 backup, §7 multi-format, §10 gaps), §11's list is
  marked item by item, decisions 23-25 are logged, the README carries a "What's
  new in 2.0" blurb and the new alacritty path, AGENTS.md's rows name the
  detect seams P5/P6 added and the no-op rule the writers now keep.

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

Implications already folded into phases: the kitty env fix (P7.1, decision
23 — `KITTY_CONFIG_DIRECTORY` primary, `KITTY_CONFIG_DIR` deprecated for
one release, alacritty's two invented vars dropped) and the Alacritty search
order, which is now upstream's own (P7.2). Alacritty `import` as a possible
push path is **not** needed for v2 — push targets whatever file currently
holds colours, which `resolve()` already finds; the `import` directive only
matters if we ever generate configs (out of scope, §3).

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
| Raw-mode exit paths multiply (prompt, overlay, save-as-new) | Closed in P5: one `enter_raw` per session and one `exit_raw` in `edit()`'s `finally`; prompts close and reopen the pair with the re-entry in a `finally`, and the SIGWINCH restore is nested inside it (§4.3 guarantee) |
| Case-insensitive FS name collisions | `create` checks case-insensitively (B) |
| Rewrite drift (HSV round-trip) | Spec TODO stays open; not a v2 blocker, edits accumulate hex-to-hex |
