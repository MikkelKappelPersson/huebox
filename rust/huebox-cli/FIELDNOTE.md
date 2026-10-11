# P6a fieldnote — `huebox-cli` (binary + composition root)

Date: 2026-10-11 · Worktree: `/home/mikkelkp/Projects/huebox-p6a` (HEAD 186e610, uncommitted)

## What landed

`rust/huebox-cli/` — clap 4 surface identical to `huebox/cli.py`, dispatch,
and the composition root wiring every editor seam. Deps: `huebox-core` +
`huebox-format` (registry names only) + `huebox-lib` (facades) +
`huebox-editor` (seam types) + `huebox-app` (`Shell`/`run`/prompter) +
`clap 4` + std only. `[[bin]]` named `huebox`; `cargo build --release`
emits the single `target/release/huebox` (2.7MB).

- `src/lib.rs` — `Cli` derive (every command/flag of `cli.py`, same help
  order), `push_spec` (same four errors, `--to` validated before anything
  runs), `Target` + theme/terminal/edit-target resolution, all five
  commands (`view`/`new`/`import`/`use`/`list`), `run_editor` (tty check
  first, then all seams, then `report_session` stdout/stderr 1:1),
  `CliLibrary` / `CliImportLibrary` / `import_writer` / backup,
  `static_preview` (see deviations), `run()` returning the exit code with
  injected `out`/`err`/`tty`/`driver` so tests never touch a real terminal.
- `src/main.rs` — 12 lines: `IsTerminal` stdin+stdout, `run(..., None)`,
  `process::exit(code)`.
- `tests/cli.rs` — 8 integration tests (surface, exit codes, headless
  seam wiring against a temp `HOME`/`XDG_CONFIG_HOME`).

## Seam wiring map (cli builds, editor owns)

| Seam | Built from |
| --- | --- |
| `write` | theme: `themes::save` (truth first) then `themes::push::<RealBackend>` with `PushOptions` from spec+name; direct: `RealBackend::write` (+ one-shot `--to`/`--ghostty-native` notes). `Err` → status string, never a panic. |
| `apply` | `themes::push::<RealBackend>` only (truth untouched); `--no-push` short-circuits with the advice line. |
| `delete_overrides` | `themes::clear_ghostty_overrides::<RealBackend>`; `Ok(0)` / `Ok(n)` (+ report + conditional re-`reload_terminal`) / `Err` → `Err(string)`. |
| `backup` | `<path>.huebox.bak` copy, best-effort bool; set only in direct mode. |
| `prompt_hex` / `prompt_name` | one `suspending_prompter()` each. |
| `Library` | `CliLibrary`: `list_themes` / `load`+`set_current` / `create` (bad-name/clash → status suffix, else notes + `could not create`). |
| `ImportLibrary` | `CliImportLibrary`: `providers::list_themes` over `provider_dirs` (shipped `$GHOSTTY_RESOURCES_DIR/themes` + user dirs under `config_home`), `read_theme`, `known().format`. |
| `ImportWriter` | `themes::create` only — never retargets, never pushes. |
| `driver` | `None` → `huebox_app::run`; tests inject a headless `FnOnce(Shell)`. |

## Deliberate deviations from `cli.py` (all tested + noted)

1. Piped `edit` exits **1**, Python exits 0. A piped session failing loudly
   beats reading as success; message identical (`huebox: not a terminal - …`).
2. `show` renders a simplified static preview (subject line, ANSI swatch
   grid with the same width-collapse ladder, named slots, edit hint) —
   the raster/mini banner art is omitted. Full art parity is worker-b
   harness territory.
3. Usage errors (`unknown action`, `--dump` combos) print
   `huebox: error: …` + exit 2 without the argparse usage dump; clap's own
   flag errors keep clap's rendering.
4. `list` stamps are UTC (same shape, `themes::system_now` math) — std has
   no localtime; differs by the UTC offset only.
5. Direct-mode write failures become `write failed: …` status instead of a
   traceback (strictly better than Python, same rule as every other seam).

## Proof

- `cargo test -p huebox-cli`: 7 lib + 8 integration = 15, green;
  workspace `cargo test`: all suites green (incl. 93 editor + 31 import);
  `cargo clippy --workspace --all-targets`: 0 warnings; `cargo fmt`: clean.
- Smoke (env override: `HOME=<tmpdir> XDG_CONFIG_HOME=<tmpdir>`):
  `huebox --help` (exit 0, all 6 commands + 12 flags),
  `huebox --version` → `huebox 0.4.1`,
  `huebox --formats` → 3 lines,
  `huebox list` (empty) → `no themes yet…`,
  `import ember --from kitty --config …` → truth file + `from kitty:…`,
  `list` → `ember  kitty  …` row,
  `--dump ember` → 22 `slot=value` lines,
  `show ember` → swatch grid + `edit interactively` hint,
  `use nosuchtheme` → `huebox: no such theme…` exit 1,
  `--to wezterm` → `huebox: unknown format 'wezterm'…` exit 1,
  piped `edit ember` → `huebox: not a terminal…` exit 1.
- `git status`: only `rust/Cargo.toml`, `rust/Cargo.lock`, `rust/huebox-cli/`.

ship and done
