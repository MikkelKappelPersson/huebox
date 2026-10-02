# huebox — tasks

Status: **all phases implemented, P7 landed** · Plan: `plan.md` ·
Spec: `spec.md` · Rules: `AGENTS.md`

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

- [x] `editor.py`: `EditorState` (slots/saved/sel/undo/status/mult/armed/written)
      + `apply_key(key, st)` pure seam; `edit()` loop slims to draw/read/apply
- [x] save semantics: Ctrl+S → write → `saved` snapshot → first-save backup;
      `r` reverts to last save; undo survives saves
- [x] Esc: dirty → armed (`Esc again to discard`), armed → quit; other keys
      disarm; Ctrl+C same path; clean Esc quits
- [x] `render.py`: `example_lines(slots)` — background / selection / cursor
      rows, all through clip/pack, rebuilt per frame (§14.1)
- [x] `editor.py`: examples strip joins the extra budget above the code sample
- [x] tests: apply_key matrix, dirty tracking, backup-once, live-everything
      (buffer change → all example rows change), frame reflects unsaved buffer
- [x] spec: §4.3 planned-note → actual semantics; open question on autosave
      answered (no, decision 15)

Notes: the write callback is `write(slots) -> status | None`, so P4 can return
`saved ember → ghostty` without touching the key switch. The backup is taken
*before* the first write (pre-save snapshot) and never refreshed. `edit()`
takes `backup=False` for files huebox owns (P3 theme files). Adjust/prompt
still no-op on a slot the config does not carry, but save/undo/revert no
longer depend on the selection. P1's "one write per key" loop assertion is
replaced by the staged model (writes only on Ctrl+S).

## P3 — theme library storage + CLI (§13.1–13.5, plan phase 3)

- [x] `themes.py`: home/themes_dir/state_path, name validation, `create`,
      `load` (gap-tolerant → MISSING + warnings), `list_themes`, `current`,
      `set_current`; lazy mkdir
- [x] `themes.py`: canonical theme writer ([theme]/[colors], created preserved,
      modified bumped, source recorded) + atomic tmp/replace write
- [x] `themes.py`: hand-rolled reader for our TOML subset (§3.2), unknown-key
      counting
- [x] `themes.py`: `RAMP` fallback palette (plan 3.3, tune before merge)
- [x] `cli.py`: actions new/list/use/import + `name` positional; `--force`,
      `--from`; `edit`/`show`/`--dump` accept a theme name; legacy fallback
      rules (§13.4)
- [x] `editor.py`: theme-mode write callback (truth file) vs legacy writer
- [x] AGENTS.md: themes.py row + dependency rule update
- [x] tests: names, round-trip, gaps/warnings, unknown keys, timestamps,
      create-refusal, list marking, state robustness, new/import seeding,
      dump-from-theme
- [x] spec: close open questions 3 (ramp values) and 5 (rm/mv enough for now)

Notes: the library is `huebox/themes.py`; the truth file is written by
`save()` from a single `name`/`created`/`modified`/`source` header plus all
22 slots palette-then-named, through `<path>.tmp` + `os.replace`. `created`
survives a re-save, `source` survives unless a new origin arrives, and a
`#rrggbb` that is not inside quotes is read as a value, not a comment
(hand-written themes look like terminal configs). `use` sets current and
prints that the terminal is not updated yet — **the push is P4**; nothing in
P3 writes a terminal config except a plain `huebox edit` in direct mode.

Two things the phase needed beyond the plan's list, both spec-mandated:
`resolve()` now infers the format from a `--config` path (§7.1 — v1
returned a `None` format there and the CLI raised `KeyError` on
`--config` without `--format`), and the `action` positional dropped
argparse `choices` so `huebox --dump ember` can bind the name (one
positional that is not a command is a theme name). Editor sessions with no
TTY on stdin/stdout say so instead of raising `termios.error`; `new` in a
pipe is now usable. The editor's closing lines for a theme session name the
theme and promise no reload — the status bar's `theme ● fmt` form is P5.

Two judgement calls for the reviewer: bare `huebox` (no action, no name)
resolves the current theme like `edit` does and falls back to the config —
otherwise a TTY run and a piped run of the same command would show
different subjects. `edit` with themes present but no current theme also
falls back to direct mode with a one-line hint; the picker (P5) is the
proper answer there. `--from` also seeds `new` when given, since "seed from
this terminal" and "import from this terminal" are the same request.

## P4 — push on save + use (§13.6, plan phase 4)

Keep the P3 review guard in mind: a future command name must not collide with
the theme-name grammar (`valid_name` decides whether an unknown positional
token is a name or an error).

P3 left the seams in place: `cli._run_editor`'s theme writer returns the
status string (`saved ember` today, `saved ember → ghostty` after the push),
and `huebox use <name>` already sets current and prints that the terminal is
untouched. Everything below replaces truth-only with truth-then-push.

- [x] `themes.py`: `push(slots, to, fmt, path, no_push)` — resolve targets,
      refuse colourless configs, line-level write, missing-key report,
      truth-first/never-rollback
- [x] `cli.py`: build the save callback from `--to`/`--no-push`; `use` =
      set_current + push (exit 1 on push failure); report lines to stderr
- [x] `editor.py`: status `saved <theme> → <fmt>` on save, push report on
      stderr after the session
- [x] tests: push fixtures (kitty/ghostty), missing-key report, --no-push
      no-op bytes, colourless-target failure after truth write, use exit codes
- [x] spec: §6.2 planned note → save pipeline; decision log entry for
      report-only push (open question 1 stays open)
- [x] README: push-on-save section

Notes: `push()` returns a `PushResult(lines, pushed, failed)` — a plain list
of report lines could not tell the caller which target failed, and text-
sniffing a report for a failure marker is the fragile coupling worth
avoiding. Targets come from `--to` (comma list, validated before any write),
else the caller's `--format`, else today's `resolve()`; `--config` pins a
single target and is refused with several (`--config names one file`).
A target whose format cannot be inferred from an explicit `--config` is
reported, not guessed (§7.1). Report lines are stderr everywhere: `use`
prints them after `  current theme: <name>`, and the editor prints them
after the session with a `huebox: ` prefix — never inside the raw-mode loop,
where they would scroll through the frame.

Two judgement calls for the reviewer. A push that cannot find a terminal at
all is a *failure* (exit 1) even when the user never named a target, because
the promise of §13.6 is "being in Ghostty means Ghostty follows" and a silent
success with no terminal touched would be a lie; `--no-push` is the escape
hatch. And gap slots push the `MISSING` grey the editor already shows — the
truth file says all 22 slots have that value, and inventing a different rule
here would make the file and the terminal disagree. The session's exit code
is the *last* save's push verdict, and `edit()` called programmatically
(no spec) writes truth only, so nothing pushes a terminal nobody named.

P5 needs to know: `_run_editor(target, spec)` now returns an exit code and
hands `report` (the list the writer fills, cleared at the top of every save)
to `edit()`, which prints it as `huebox: <line>` on stderr after the closing
`  saved theme <name>  <path>` line. The picker should save through the same
`write` callback, so it inherits the push and the reset for free. Also: no-op pushes leave the config byte-identical but still bump its
mtime — §6.2 rule 4 ("must not rewrite the file at all") is unmet by
`write_flat` in v1 as well, so it is a P7 hygiene item, not a P4 one.

## P5 — TUI picker + save-as-new (§13.7, plan phase 5)

- [x] reset `armed` when the overlay loads another theme (P2 review note:
      the armed flag persists post-quit by design — harmless there, but a
      fresh theme must not inherit a pending discard)
- [x] `editor.py`: `t` overlay (list/pack rows, arrows, Enter opens + sets
      current, `n` new-from-buffer via name prompt, Esc back)
- [x] dirty-switch block with `save (Ctrl+S) or revert (r) first`
- [x] `N` save-as-new (name prompt, existing-name confirm) — the legacy
      migration path
- [x] status bar: `theme ● fmt` / `direct:<path>` + dirty dot
- [x] tune MIN sizes (P1 TODO closes); overlay under too-small check
- [x] tests: overlay apply_key flows, dirty block, save-as-new, overlay
      rendering within width budget
- [x] spec: close §4.3 raw-mode TODO (one enter/exit pair rule, prompt paths
      enumerated); AGENTS.md guideline if patterns changed

Notes: the picker is the one place where the session's *subject* changes, and
that rippled into two seams. `edit()`'s writer is now called as
`write(theme, path, slots)` — the name travels with every save because a
switch mid-session has to retarget `Ctrl+S`; cli's single writer branches on
it (`None` = the v1 direct-mode config write, a name = truth-then-push), so
`N` can migrate a direct session without the writer ever seeing a format
mismatch. Two existing test helpers (`EditLoop.run_session`,
`ThemeSession.session`) were updated for that signature — no assertions were
weakened.

The library arrives as an injected `editor.Library` (list / load / create),
not an import: `editor` still depends only on `render` + `tui` + `color`, and
every failure comes back as a status line instead of an exception into the
draw loop. Load-time complaints a status bar cannot hold — a theme that would
not open, a state file that could not be written, dropped keys and grey gaps —
are collected in a `notes` list and printed after the session on stderr,
exactly like the push report.

Three judgement calls for the reviewer. **The picker replaces the frame**
rather than insetting a box (decision 19): one frame means one layout budget,
its widest row is a name `clip` truncates, and its hint footer folds through
`pack` — which is also why `MIN_COLS`/`MIN_ROWS` stay 40x12 (§15.4). While
the picker is up it owns the whole key surface, so `Q` and `Ctrl+C` close it
instead of quitting: a stray key cannot quit or edit behind a list you are
reading. And **creating adopts**: `n` (in the picker) and `N` (in the editor)
both make the new theme the session's subject and the library's current, with
`N` then running the ordinary save pipeline — one writer, one push contract,
and the status bar always names the theme the next `Ctrl+S` will write. The
undo log is cleared on adoption (the buffer and the new file are identical, so
there is nothing to undo into) and `.huebox.bak` no longer applies.

`<fmt>` in the status bar is the push target the command line named (`-f`, or
the first of `--to`); with neither flag there is no target to name until a save
resolves one, and the save's own status line names every format it pushed. The
editor frame's key-hint line now packs two spaces apart: the two new keys would
otherwise have cost the examples strip a row at 60x24, which P2's test guards.

Raw mode (§4.3, now a guarantee instead of a TODO): one `enter_raw` per
session, one `exit_raw` in `edit()`'s `finally`, every prompt closing and
reopening the pair with its re-entry in a `finally` (Ctrl+C/EOF cancel the
prompt and return to the editor), and the SIGWINCH handler restored in a
nested block that runs even if the termios restore itself raises — the old
`finally` could leave the handler installed when `exit_raw` threw. The overlay
is not a fifth path: it is drawn and read inside the same loop. `tests/
test_editor.py::RawMode` asserts the whole list by pairing every exit with
the enter whose state it restores.

## P6 — Ghostty native export (§13.6, plan phase 6)

- [x] `themes.py`: `export_ghostty_native(name, slots)` →
      `$XDG_CONFIG_HOME/ghostty/themes/<name>` via write_flat + GHOSTTY_RULES
      template (tmp → splice → replace; `valid_name` guard; gaps as MISSING)
- [x] `detect.py`: `ensure_theme_pointer(config_path, name)` — rewrite or
      append `theme = name`, line-level, `unchanged`/`rewritten`/`appended`
- [x] `detect.py`: `ghostty_main_config()` (the file that *holds* the
      pointer) + `config_holds_colours()` (§7.3 asked of a chain), because
      the pointer target is deliberately not the file `resolve()` follows
- [x] `cli.py`: `--ghostty-native` flag wiring — `PushSpec.ghostty_native`
      with a `False` default, `push(..., ghostty_native, name)`
- [x] tests: exported round-trip, pointer rewrite byte-exact except theme
      line, append case (with/without trailing newline), CRLF, kitty and
      alacritty unaffected, illegal name, CLI `use`/`edit` end to end
- [x] spec: close open question 2 with the decision; decision log entry
- [x] **P6b (decision 26)**: the export becomes the default for a config
      that is organised by theme. `detect.ghostty_theme_name()` (the
      pointer's value, with no "does the file exist" step) against the
      resolved target decides the path; `--ghostty-native` forces the
      export, `--ghostty-in-place` forces the edit and the two are refused
      together; `themes._foreign_theme()` refuses an in-place write into
      another theme's file, so the invariant holds under every flag
      combination; the report says when an export shadows a config's own
      inline colours; `--no-push` wins over both flags
- [x] P6b tests: a save under a theme-config exports its own file, repoints
      the config and leaves the old theme file byte-identical; a save of the
      theme already in use stays on its own file; in-place is refused when
      the file is another theme's, allowed when the push changes nothing,
      and allowed for inline colours; the refusal and the end-to-end `use`
      hold through the CLI; a dangling `theme =` is repaired end to end and
      still refused with an actionable line under `--ghostty-in-place` or
      with no name; another format's missing config does not reach for
      ghostty; a test home of its own for the CLI subprocess suite, which
      was reading the real `~/.config`

Notes: the decision is **the export by default where the config is
organised by theme** (decision 26, superseding decision 22's opt-in) —
writing one theme's colours into another theme's file is the failure mode
that matters, and one `theme =` swap is visible, reversible and reported.
A `theme =` naming a file that is missing is a broken chain, not a
colourless config, so a save with a theme name repairs it: export the
theme, repoint the config, and say the file was missing. The export is
ghostty-scoped: `--to ghostty,kitty` exports for ghostty and pushes kitty
the phase-1 way, and a legacy direct session (no theme, so no name to
export under) writes the config and says why in the post-session `notes`
list.

Two seams the plan's bullets did not name, both forced by §7.
`ghostty_main_config()` exists because the pointer belongs in the file that
*holds* the `theme =` line, which is not the file `resolve()` follows to the
colours — with `--config` it is the named file, otherwise the first Ghostty
candidate that exists. And `config_holds_colours()` exists because §7.3's
"only offer a terminal whose config actually contains colours" has to be
asked of the whole chain on this path: a main config holding nothing but
`theme = ember` is a real terminal, and one pointing at a missing theme
file is not. Both are tested directly.

One pre-existing read bug had to be fixed to make the writer testable
against the reader: `_ghostty_theme_file` took the rest of the line as the
theme name, so `theme = ember   # mine` resolved to a file called
`ember"   # mine`. Reader and writer now share one `THEME_LINE` regex, with
its own `cr` group so a CRLF config keeps its line endings, and
`ensure_theme_pointer` opens with `newline=""` both ways (universal newlines
would rewrite a CRLF file; `surrogateescape` carries a non-UTF-8 one through
byte for byte). No push behaviour changes for configs whose theme value is
a single word — the 204 pre-existing tests pass untouched. The THEME_LINE
reader also gained what v1 never had: space-named values, bare or quoted
(`theme = Catppuccin Mocha`), now follow to their theme files instead of
falling through to the shipped-theme copy.

P7 note: the export goes through `write_flat`, so it still leaks the
`ResourceWarning` the `with open` sweep is meant to kill — the sweep should
cover `formats/base.py`, not only the files this phase touched.

## P6c — the reload, and a theme switch that is a save (decision 27)

- [x] `themes.py`: `reload_terminal(fmt)` — ghostty via the `SIGUSR2` its
      own application handles (`ghostty_app_pid()` walks `/proc` for the
      `--gtk-single-instance` process, so a build with per-window surfaces
      is not mis-signalled), kitty via `kitty @ load-config` with all three
      streams on `DEVNULL` and a timeout; `""` for anything that cannot be
      told. Best effort by contract: the bytes are on disk before it runs
- [x] `themes.py`: `PushResult.reloaded`, so the caller knows whether it
      still owes the "reload your terminal" advice; `push(reload=…)`
      defaults off, so a programmatic push never reaches for a signal
- [x] `cli.py`: `--no-reload`; `PushSpec.reload`; both push call sites
- [x] `editor.py`: `Enter` in the picker runs `save_state()` — opening a
      theme is a save, so the theme you pick is the one on screen
- [x] tests: signal vs surface selection, a signal that lands nowhere, the
      kitty command with its three streams, a format with no interface, a
      failed push not reloading, the CLI default vs `--no-reload`, and the
      picker's `Enter` pushing without a `Ctrl+S`. The CLI harness passes
      `--no-reload` throughout, because the reload reaches for the
      *machine's* ghostty
- [x] spec: §13.6 reload, §13.7 Enter, §13.4 current, §4.2 flag, decision
      27

## P7 — hygiene + release (plan phase 7)

- [x] `detect.py`: `KITTY_CONFIG_DIRECTORY` (old spelling secondary);
      verify/drop `ALACRITTY_CONFIG_DIR`/`ALACRITTY_CONFIG`
- [x] Alacritty search order gains `$XDG_CONFIG_HOME/alacritty.toml`
- [x] no-op push must not rewrite the config at all — byte-identical AND
      mtime-stable (§6.2 rule 4; pre-existing `write_flat` behaviour, logged
      by the P4 review)
- [x] `_ghostty_includes` still parses `config-file = path  # comment` the
      old sloppy way — same class of bug the P6 `THEME_LINE` fix removed
      for `theme =` (logged by the P6 review)
- [x] `with open(...)` sweep — silence ResourceWarnings (zero-behaviour)
- [x] README/AGENTS/spec full sync; version → 0.2.0; changelog blurb
- [x] final: full suite + `uv build` + CLI round-trip in a clean env + one
      real editor session driven through a pty (edit, save+push, quit)

Notes: the two code fixes are both in the place the P4/P6 reviews said the
problem lived — the writers, not the push path. `write_flat` and `write_toml`
now compare the line list they just built and return before opening the file
for writing, so §6.2 rule 4 is a property every caller inherits (the native
export's tmp splice included). The existing byte-identity test grew an mtime
assertion and `Push` gained a no-op push case plus a write-failure case
(§10's "unwritable config"), and both fail against the v1 writers — verified
by stashing the two writer files and watching them go red.

`_ghostty_includes` reads the value the way the P6 fix taught `theme =` to be
read: quoted or bare, bare stops at the first `#`, `?path` still relative to
the config. `CONFIG_FILE_LINE` sits next to `THEME_LINE` in `detect.py` for
exactly that reason. Five cases: commented include, quoted path with spaces,
`?`-relative, commented-out line, missing file.

The fd sweep was six `open()` calls in the package (two in `formats/base.py`,
two in `formats/alacritty.py`, two in `detect.py`) plus three in
`tests/test_formats.py`. Suite ResourceWarnings: **119 → 0**, verified with
`python3 -W always -m unittest discover -s tests`, zero behaviour change.

Env vars follow upstream's spelling and nothing else (decision 23):
`KITTY_CONFIG_DIRECTORY` first, `KITTY_CONFIG_DIR` second and marked
deprecated for one release, alacritty's two invented vars dropped because
Alacritty documents no config-path variable at all. The drop is user-visible,
so it is stated in the spec (§6, §7.2, decision 23), the README's 2.0 blurb
and plan Appendix A rather than done quietly.

Docs: spec TODOs now carry their answers as `>` notes (§5.1 rounding and the
140 threshold, §6.2 backup, §7 multi-format, §10 gaps), §11's list is marked
item by item — open questions 1 (push-insert-keys) and §13.8 question 4
(`list --porcelain`) stay open on purpose — decisions 23-25 are logged, README
carries the 2.0 blurb plus the new alacritty path and the full flag list,
AGENTS.md's `detect` row names the P5/P6 seams and two new guidelines cover
the no-op rule and `with open`. plan.md has the as-built notes.

Suite: 253 tests, green twice, no warnings. `uv build` produces
`huebox-0.2.0.tar.gz` + wheel with every module and the console script.
Clean-env round-trip: `--help`, `--version` (`huebox 0.2.0`), `import` →
`list` → `use --no-push` → `list` (current marked) → `--dump smoke`, plus a
real push whose second run left the config's mtime untouched, and
`resolve()` finding alacritty at `$XDG_CONFIG_HOME/alacritty.toml`, kitty via
both env spellings, and ignoring `ALACRITTY_CONFIG`.

There is no ghostty on this machine, so the interactive smoke ran through a
`pty` (stdlib) instead of a hand-driven session: `huebox edit smoke` at
100x30, `x` nudged the buffer (the header picked up the `●`), `Ctrl+S` saved
and pushed (`palette-0` went `#0a0a13` → `#0d0d18` in both the theme file and
the config, the dot cleared), `Esc` quit with status 0, and a second session
that saved without changing anything left the config's mtime untouched.

## P8 — the live diff widget (spec §14.4, decision 28)

- [x] `render.py`: `diff_lines(slots, cols)` — a git hunk over the zig
      sample, four slots and no lexer (`palette-1` removed, `palette-2`
      added, `palette-6` `@@`, `palette-8` the hunk's tail)
- [x] `editor.py`: the block's own label, `DIFF_ROWS` / `DIFF_FLOOR`, drawn
      above the sample out of the rows the sample did not need (§15)
- [x] a cut hunk loses a whole pair, never half of one
- [x] `__init__.py` re-export; tests: `Diff` in `tests/test_render.py` and
      the ladder in `tests/test_editor.py` (whole at 120x40, floor at 110x36,
      absent — with the v1 strip and sample numbers — everywhere below)
- [x] spec §8 / §14.4 / §15 / §10 / §16 / decision 28, README, AGENTS.md

Notes: the question was where a diff belongs — inside the code sample or
beside it. Inside would have cost the sample half its vocabulary: a zig lexer
emits no `+` and `-`, so the two languages would have shared one mapping
table and one block, and neither reads. Beside it, the diff spends the two
slots §8 says nothing else can (`palette-1`, `palette-2`) without the sample
giving up a single line.

Where the hunk sits in the ladder was the open question, and the answer the
user picked is the one that costs nothing: the hunk fills rows the sample did
not need and never takes one from it, so the ladder is unchanged at every size
huebox shipped at — 100x30 keeps its seven sample lines, 80x24 its four — and
the hunk appears only in frames with room to spare (whole at 120x40, at its
floor at 110x36). Verified by rendering both trees at nineteen sizes and
diffing: apart from the added hunk rows, every frame is byte-identical to the
one before this phase. `WIDGET_BUDGET` is deliberately left at the v1
number: promising the hunk's rows there would spend the palette legend on a
widget the frame cannot show.

An earlier cut of this phase put the hunk *above* the sample in the spending
order, which cost the sample four lines at 100x30 and three at 80x24. Same
widget, same slots, one line of allocation apart — which is why the ladder is
a decision (28) and not an implementation detail.
