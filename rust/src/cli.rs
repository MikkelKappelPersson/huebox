//! The command line: clap surface, dispatch, and the composition root.
//!
//! Rust port of `huebox/cli.py`. `cli` is the composition root: it builds
//! every injected seam the editor reaches the world through (save/apply
//! writers via [`library`] `save`/`push` with [`RealBackend`], the
//! Ghostty overrides deleter via `clear`, `prompt_hex`/`prompt_name` via
//! the app's suspending prompter, the picker's [`CliLibrary`], the import
//! popup's [`CliImportLibrary`] plus the `create` writer, the pre-save
//! backup), constructs the app [`Shell`](crate::app::Shell), runs it, and
//! prints [`report_session`](crate::editor::report_session) lines 1:1.
//!
//! Non-editor commands never touch the editor (fast cold start — the
//! backend is built late, like the Python late `app` import). The tty
//! check runs before any of it, so a piped session reports the real
//! problem. User errors print `huebox: {msg}` on stderr and exit 1, never
//! a traceback; usage errors exit 2 as `huebox: error: ...`, including
//! clap's own (bad values, unknown flags) whose body is kept verbatim
//! behind our prefix. `--format`/`--from` are clamped by clap before any
//! lookup (which would panic on an unknown name).

use std::cell::RefCell;
use std::io::{IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::rc::Rc;

use crate::app::Shell;
use crate::core::Slots;
use crate::editor::{
    enter_setup, report_session, ApplyFn, BackupFn, DeleteFn, EditorState, ImportLibrary,
    ImportWriter, Library, PromptFn, WriteFn, WriteOutcome,
};
use crate::library::backend::RealBackend;
use crate::library::format::{unknown_format, FormatStub};
use crate::library::{detect, providers, themes, SlotMap, SLOTS};
use crate::render::{banner, chrome, Row};
use clap::Parser;

/// What a command works on: a theme (by name) or a terminal config.
#[derive(Clone, Debug)]
pub struct Target {
    /// Theme being edited (`None`: the terminal config is the subject).
    pub theme: Option<String>,
    /// `theme {name}` or the format name.
    pub label: String,
    /// Truth file or terminal config.
    pub path: PathBuf,
    /// Resolved colours.
    pub slots: SlotMap,
}

/// Where a save pushes, from `--to` / `--format` / `--config` / `--no-push`.
#[derive(Clone, Debug, Default)]
pub struct PushSpec {
    /// Push targets (empty: the terminal you are in).
    pub to: Vec<String>,
    /// Forced terminal format.
    pub fmt: Option<String>,
    /// Explicit config path.
    pub path: Option<PathBuf>,
    /// Write the theme file only.
    pub no_push: bool,
    /// Always push ghostty as a theme file.
    pub ghostty_native: bool,
    /// Never export a ghostty theme file.
    pub ghostty_in_place: bool,
    /// Ask pushed terminals to re-read their configs.
    pub reload: bool,
}

/// The two lines that are advice rather than report.
pub const NO_PUSH_LINE: &str = "no push requested (--no-push) - the terminal is unchanged";
/// The one line of advice a written config needs.
pub const RELOAD_HINT: &str = "reload your terminal to see it (alacritty reloads by itself)";

/// One-word commands, in help order.
pub const ACTIONS: [&str; 6] = ["show", "edit", "new", "list", "use", "import"];

/// The session driver: the Textual-shell equivalent. Production passes
/// [`crate::app::run`]; tests drive the same [`Shell`] headless.
pub type Driver = Box<dyn FnOnce(Shell) -> std::io::Result<Shell>>;

/// clap surface, identical to the argparse one in `cli.py`.
#[derive(Parser, Debug)]
#[command(
    name = "huebox",
    version,
    about = "show and edit your terminal's colour theme"
)]
pub struct Cli {
    /// One of show, edit, new, list, use, import (default: edit on a TTY, show off one)
    pub action: Option<String>,
    /// Theme name: new/import/use take one, edit/show/--dump read one
    pub name: Option<String>,
    /// Force a terminal format instead of detecting one
    #[arg(short = 'f', long = "format", value_parser = ["ghostty", "kitty", "alacritty"])]
    pub format: Option<String>,
    /// Path to the config file
    #[arg(short = 'c', long = "config")]
    pub config: Option<String>,
    /// Replace an existing theme (new/import)
    #[arg(long = "force")]
    pub force: bool,
    /// Terminal format to read from (import, and the seed for new)
    #[arg(long = "from", value_name = "FMT", value_parser = ["ghostty", "kitty", "alacritty"])]
    pub from_fmt: Option<String>,
    /// Push targets for a theme save or `use` (default: the terminal you are in)
    #[arg(long = "to", value_name = "FMT[,FMT...]")]
    pub to: Option<String>,
    /// Write the theme file only, leave the terminal config alone
    #[arg(long = "no-push")]
    pub no_push: bool,
    /// Always push ghostty as a theme file, even where the colours are inline
    #[arg(long = "ghostty-native")]
    pub ghostty_native: bool,
    /// Never export a ghostty theme file: edit the file the colours hold
    #[arg(long = "ghostty-in-place")]
    pub ghostty_in_place: bool,
    /// Do not ask the terminal to re-read its config after a push
    #[arg(long = "no-reload", action = clap::ArgAction::SetFalse)]
    pub reload: bool,
    /// Print the resolved colours as key=value and exit
    #[arg(long = "dump")]
    pub dump: bool,
    /// List supported formats and exit
    #[arg(long = "formats")]
    pub formats: bool,
    /// Machine-readable theme list (`list` only): `name \t path \t mtime-epoch`
    #[arg(long = "porcelain")]
    pub porcelain: bool,
}

// ---------------------------------------------------------------------------
// entry point (returns the exit code; `main` exits with it)
// ---------------------------------------------------------------------------

/// Parse `argv` and run the command. `tty` is stdin+stdout both terminals
/// (production reads `IsTerminal`); `driver` runs the editor session
/// (`None`: the real compositor via [`crate::app::run`]).
pub fn run(
    argv: Vec<String>,
    out: &mut dyn Write,
    err: &mut dyn Write,
    tty: bool,
    driver: Option<Driver>,
) -> i32 {
    let cli = match Cli::try_parse_from(&argv) {
        Ok(cli) => cli,
        Err(error) => {
            let code = error.exit_code();
            // `--help`/`--version` read as stdout; usage errors as stderr.
            if error.use_stderr() {
                // Usage errors exit 2 as `huebox: error: ...` — clap's
                // body (possible values, usage) is kept, only the
                // prefix is ours.
                let rendered = format!("{error}");
                let body = rendered.strip_prefix("error: ").unwrap_or(&rendered);
                let _ = write!(err, "huebox: error: {body}");
            } else {
                let _ = write!(out, "{error}");
            }
            return code;
        }
    };

    let mut action = cli.action.clone();
    let mut name = cli.name.clone();
    if let Some(given) = action.clone() {
        if !ACTIONS.contains(&given.as_str()) {
            // `huebox --dump ember` puts the theme in the action slot.
            if name.is_none() && themes::valid_name(&given) {
                name = Some(given);
                action = None;
            } else {
                return fail2(
                    err,
                    format!(
                        "unknown action '{given}' (choose from {})",
                        ACTIONS.join(", ")
                    ),
                );
            }
        }
    }
    if cli.dump
        && matches!(
            action.as_deref(),
            Some("use") | Some("list") | Some("new") | Some("import")
        )
    {
        return fail2(
            err,
            format!(
                "--dump cannot be combined with {}",
                action.as_deref().unwrap_or("")
            ),
        );
    }
    // `--porcelain` is a `list` output shape, not a global flag.
    if cli.porcelain && action.as_deref() != Some("list") {
        return fail2(err, "--porcelain needs list".to_owned());
    }
    if cli.porcelain && cli.formats {
        return fail2(
            err,
            "--porcelain cannot be combined with --formats".to_owned(),
        );
    }

    if cli.formats {
        for format in RealBackend::format_names() {
            let _ = writeln!(out, "{format}");
        }
        return 0;
    }

    // Push targets validate before anything runs: a bad `--to` must never
    // be discovered half a save in.
    let spec = match push_spec(&cli) {
        Ok(spec) => spec,
        Err(message) => return fail(err, message),
    };

    match action.as_deref() {
        None => {
            // Bare `huebox`: the thing you are working on, edited on a TTY
            // and previewed off one.
            let verb = if tty { "edit" } else { "show" };
            cmd_view(
                &cli,
                name.as_deref(),
                verb,
                &spec,
                true,
                out,
                err,
                tty,
                driver,
            )
        }
        Some("list") => {
            // `list` takes no theme: a stray positional is a typo for
            // `show`/`use`, not a filter, so it is a usage error.
            if name.is_some() {
                return fail2(err, "list takes no theme name".to_owned());
            }
            cmd_list(out, cli.porcelain)
        }
        Some("new") => cmd_new(&cli, name.as_deref(), &spec, out, err, tty, driver),
        Some("import") => cmd_import(&cli, name.as_deref(), out, err),
        Some("use") => cmd_use(&cli, name.as_deref(), &spec, out, err, tty),
        Some(_) => cmd_view(
            &cli,
            name.as_deref(),
            action.as_deref().unwrap_or("show"),
            &spec,
            false,
            out,
            err,
            tty,
            driver,
        ),
    }
}

/// `huebox: {message}` on stderr, exit 1. Never a traceback.
fn fail(err: &mut dyn Write, message: String) -> i32 {
    let _ = writeln!(err, "huebox: {message}");
    1
}

/// `huebox: error: {message}` on stderr, exit 2 (the argparse `parser.error`
/// shape, minus the usage dump).
fn fail2(err: &mut dyn Write, message: String) -> i32 {
    let _ = writeln!(err, "huebox: error: {message}");
    2
}

/// `huebox: {message}` on stderr, no exit (a warning inside a command).
fn warn(err: &mut dyn Write, message: &str) {
    let _ = writeln!(err, "huebox: {message}");
}

/// The complaint for a bad or missing name argument, or `None`.
fn name_problem(name: Option<&str>, verb: &str) -> Option<String> {
    match name {
        None => Some(format!("{verb} needs a theme name")),
        Some(name) if !themes::valid_name(name) => Some("invalid theme name".to_owned()),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// where a save pushes
// ---------------------------------------------------------------------------

/// `--to` / `--format` / `--config` / `--no-push` as one push target.
pub fn push_spec(cli: &Cli) -> Result<PushSpec, String> {
    let mut to: Vec<String> = Vec::new();
    for part in (cli.to.clone().unwrap_or_default()).split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        if !RealBackend::format_names().contains(&part) {
            return Err(unknown_format(part, RealBackend::format_names()));
        }
        if !to.iter().any(|seen| seen == part) {
            to.push(part.to_owned());
        }
    }
    if cli.to.is_some() && to.is_empty() {
        return Err("--to needs at least one format".to_owned());
    }
    if cli.config.is_some() && to.len() > 1 {
        return Err("--config pushes one format; --to names several - \
                    drop --config or keep a single --to"
            .to_owned());
    }
    if cli.ghostty_native && cli.ghostty_in_place {
        return Err(
            "--ghostty-native and --ghostty-in-place ask for opposite things - pick one".to_owned(),
        );
    }
    Ok(PushSpec {
        to,
        fmt: cli.format.clone(),
        path: cli.config.as_ref().map(PathBuf::from),
        no_push: cli.no_push,
        ghostty_native: cli.ghostty_native,
        ghostty_in_place: cli.ghostty_in_place,
        reload: cli.reload,
    })
}

/// A push report plus the one line of advice a written config needs.
fn push_lines(result: &themes::PushResult) -> Vec<String> {
    let mut lines = result.lines.clone();
    // `needs_reload_advice` knows which terminals live-reload (decision 53:
    // an alacritty-only push owes no hint) — the callers never re-decide it.
    if result.needs_reload_advice() {
        lines.push(RELOAD_HINT.to_owned());
    }
    lines
}

/// A y/n answer: `y`/`yes`, case-insensitive — the same word the theme
/// overwrite prompt takes, so one yes means yes everywhere.
fn is_yes(answer: &str) -> bool {
    matches!(answer.trim().to_lowercase().as_str(), "y" | "yes")
}

/// `ghostty, kitty` — the format names a status line names.
fn pushed_names(result: &themes::PushResult) -> String {
    result
        .pushed
        .iter()
        .map(|(fmt, _)| fmt.clone())
        .collect::<Vec<_>>()
        .join(", ")
}

// ---------------------------------------------------------------------------
// picking what to work on
// ---------------------------------------------------------------------------

/// A theme by name, or `None` after reporting why not.
fn theme_target(name: &str, err: &mut dyn Write) -> Option<Target> {
    if !themes::valid_name(name) {
        fail(err, "invalid theme name".to_owned());
        return None;
    }
    let path = match themes::theme_path(name) {
        Ok(path) => path,
        Err(error) => {
            fail(err, error.0);
            return None;
        }
    };
    match themes::load(name) {
        Ok((slots, warnings)) => {
            for warning in &warnings {
                warn(err, warning);
            }
            Some(Target {
                theme: Some(name.to_owned()),
                label: format!("theme {name}"),
                path,
                slots,
            })
        }
        Err(error) => {
            fail(err, error.0);
            None
        }
    }
}

/// `resolve()` plus the one case it hands back without a format.
fn resolve_trio(
    fmt: Option<&str>,
    path: Option<&str>,
) -> (Option<String>, Option<PathBuf>, Option<String>) {
    let (fmt, path, error) = detect::resolve::<RealBackend>(fmt, path.map(Path::new));
    if error.is_some() {
        return (None, None, error);
    }
    if fmt.is_none() {
        return (
            None,
            None,
            Some(format!(
                "cannot tell which format this config is - pass --format with one of {}",
                RealBackend::format_names().join(", ")
            )),
        );
    }
    (fmt, path, None)
}

/// The v1 subject: the resolved terminal config and its colours.
fn terminal_target(fmt: Option<&str>, path: Option<&str>, err: &mut dyn Write) -> Option<Target> {
    let (fmt, path, error) = resolve_trio(fmt, path);
    let (fmt, path) = match (fmt, path, error) {
        (Some(fmt), Some(path), _) => (fmt, path),
        (_, _, Some(error)) => {
            fail(err, error);
            return None;
        }
        _ => {
            fail(err, "cannot tell which format this config is".to_owned());
            return None;
        }
    };
    let slots = RealBackend::read(&fmt, &path);
    if slots.is_empty() {
        fail(err, format!("no colours found in {}", path.display()));
        return None;
    }
    Some(Target {
        theme: None,
        label: fmt,
        path,
        slots,
    })
}

/// `edit [name]`: a named theme, the current one, or direct mode.
fn edit_target(cli: &Cli, name: Option<&str>, err: &mut dyn Write) -> Option<Target> {
    if let Some(name) = name {
        return theme_target(name, err);
    }
    match themes::current() {
        None => {
            if !themes::list_themes().is_empty() {
                warn(
                    err,
                    "no current theme set - editing the terminal config directly \
                     (huebox use <name> to pick one)",
                );
            }
            terminal_target(cli.format.as_deref(), cli.config.as_deref(), err)
        }
        Some(current) => {
            let missing = themes::theme_path(&current)
                .map(|path| !path.is_file())
                .unwrap_or(true);
            if missing {
                warn(
                    err,
                    &format!(
                        "current theme {current} no longer exists - \
                         editing the terminal config directly"
                    ),
                );
                return terminal_target(cli.format.as_deref(), cli.config.as_deref(), err);
            }
            theme_target(&current, err)
        }
    }
}

// ---------------------------------------------------------------------------
// slot conversions (SlotMap <-> Slots)
// ---------------------------------------------------------------------------

/// Canonical map into the editor buffer (all keys here are canonical, so
/// `set` never sees a name outside the model).
pub fn to_slots(map: &SlotMap) -> Slots {
    let mut slots = Slots::default();
    for (name, value) in map {
        slots.set(name, value);
    }
    slots
}

/// Editor buffer back into a canonical map.
pub fn to_slotmap(slots: &Slots) -> SlotMap {
    slots
        .iter()
        .map(|(name, value)| (name.to_owned(), value.to_owned()))
        .collect()
}

// ---------------------------------------------------------------------------
// running the editor (the composition root)
// ---------------------------------------------------------------------------

/// The picker onto the theme store: three calls, no import.
#[derive(Clone, Debug, Default)]
pub struct CliLibrary {
    /// Session notes a one-line status bar cannot carry.
    pub notes: Rc<RefCell<Vec<String>>>,
}

impl CliLibrary {
    /// A picker sharing `notes` with the session report.
    pub fn shared(notes: &Rc<RefCell<Vec<String>>>) -> Self {
        Self {
            notes: Rc::clone(notes),
        }
    }
}

impl Library for CliLibrary {
    fn names(&self) -> Vec<String> {
        themes::list_themes()
            .into_iter()
            .map(|(name, _, _)| name)
            .collect()
    }

    fn load(&self, name: &str) -> Option<(Slots, String)> {
        let (slots, warnings) = match themes::load(name) {
            Ok(loaded) => loaded,
            Err(error) => {
                self.notes.borrow_mut().push(error.0);
                return None;
            }
        };
        if let Err(error) = themes::set_current(name) {
            self.notes
                .borrow_mut()
                .push(format!("cannot write state: {}", error.0));
        }
        self.notes.borrow_mut().extend(warnings);
        let path = themes::theme_path(name).ok()?;
        Some((to_slots(&slots), path.to_string_lossy().into_owned()))
    }

    fn create(&self, name: &str, slots: &Slots, force: bool) -> Result<String, String> {
        let path = match themes::create(name, &to_slotmap(slots), None, force) {
            Ok(path) => path,
            Err(error) => {
                if error.0 == "invalid theme name" || error.0.contains("already exists") {
                    return Err(format!("{} (letters, digits, - and _, 64 max)", error.0));
                }
                self.notes
                    .borrow_mut()
                    .push(format!("could not create {name}: {}", error.0));
                return Err(format!("could not create {name}"));
            }
        };
        if let Err(error) = themes::set_current(name) {
            self.notes.borrow_mut().push(format!(
                "created {name} but the state file could not be written: {}",
                error.0
            ));
            return Ok(path.to_string_lossy().into_owned());
        }
        Ok(path.to_string_lossy().into_owned())
    }
}

/// Local dirs `provider` reads, in order: shipped first, user last.
pub fn provider_dirs(provider: &str) -> Vec<PathBuf> {
    if provider == "ghostty" {
        let resources = std::env::var("GHOSTTY_RESOURCES_DIR")
            .unwrap_or_else(|_| "/usr/share/ghostty".to_owned());
        return vec![
            PathBuf::from(resources).join("themes"),
            detect::ghostty_themes_dir(),
        ];
    }
    let base = detect::config_home();
    match provider {
        "kitty" => vec![
            base.join("kitty/themes"),
            base.join("kitty/kitty-themes/themes"),
        ],
        "alacritty" => vec![base.join("alacritty/themes")],
        _ => vec![],
    }
}

/// Provider themes for the import popup: list + read, injected.
#[derive(Clone, Debug, Default)]
pub struct CliImportLibrary {
    /// Session notes a skipped file joins.
    pub notes: Rc<RefCell<Vec<String>>>,
}

impl CliImportLibrary {
    /// An import source sharing `notes` with the session report.
    pub fn shared(notes: &Rc<RefCell<Vec<String>>>) -> Self {
        Self {
            notes: Rc::clone(notes),
        }
    }
}

impl ImportLibrary for CliImportLibrary {
    fn entries(&self, provider: &str) -> Vec<(String, String)> {
        let (entries, file_notes) =
            match providers::list_themes::<RealBackend>(provider, Some(&provider_dirs(provider))) {
                Ok(listing) => listing,
                Err(_) => return vec![],
            };
        {
            let mut notes = self.notes.borrow_mut();
            for note in file_notes {
                if !notes.contains(&note) {
                    notes.push(note);
                }
            }
        }
        entries
            .into_iter()
            .map(|(display, path)| (display, path.to_string_lossy().into_owned()))
            .collect()
    }

    fn read(&self, provider: &str, path: &str) -> Option<Slots> {
        let slots = providers::read_theme::<RealBackend>(provider, Path::new(path)).ok()?;
        if slots.is_empty() {
            return None;
        }
        Some(to_slots(&slots))
    }

    fn format_of(&self, provider: &str) -> String {
        providers::known(provider)
            .map(|def| def.format.to_owned())
            .unwrap_or_else(|_| provider.to_owned())
    }
}

/// One confirmed import as a truth file: `themes.create`, nothing else.
pub fn import_writer() -> ImportWriter {
    Box::new(move |name: &str, slots: &Slots, source: &str| {
        themes::create(name, &to_slotmap(slots), Some(source), false)
            .map(|_| ())
            .map_err(|error| error.0)
    })
}

/// Pre-save snapshot: `<path>.huebox.bak`. Best-effort, never blocks a save.
fn backup_fn() -> BackupFn {
    Box::new(move |path: &str| {
        let backup = format!("{path}.huebox.bak");
        std::fs::copy(path, &backup).is_ok()
    })
}

/// Push options for one session: the spec plus the subject's name.
fn push_options(spec: &PushSpec, theme: Option<&str>) -> themes::PushOptions {
    themes::PushOptions {
        to: spec.to.clone(),
        fmt: spec.fmt.clone(),
        path: spec.path.clone(),
        no_push: spec.no_push,
        ghostty_native: spec.ghostty_native,
        name: theme.map(str::to_owned),
        ghostty_in_place: spec.ghostty_in_place,
        reload: spec.reload,
    }
}

/// Open the editor; theme mode writes truth, then pushes.
///
/// `driver` runs the [`Shell`] (production: [`crate::app::run`]). Seams,
/// report, and exit code mirror `_run_editor` in `cli.py`.
#[allow(clippy::too_many_arguments)]
pub fn run_editor(
    target: &Target,
    spec: &PushSpec,
    out: &mut dyn Write,
    err: &mut dyn Write,
    tty: bool,
    driver: Option<Driver>,
) -> i32 {
    if !tty {
        // A pipe or a file: no terminal to drive, and no traceback either.
        let command = match &target.theme {
            Some(theme) => format!("huebox edit {theme}"),
            None => "huebox edit".to_owned(),
        };
        warn(
            err,
            &format!("not a terminal - run `{command}` in a terminal"),
        );
        // cli.py returns 0 here and spec §2 pins identical exit codes,
        // so the crate reports the problem and exits 0 too.
        return 0;
    }

    let report: Rc<RefCell<Vec<String>>> = Rc::default();
    let notes: Rc<RefCell<Vec<String>>> = Rc::default();
    let failed: Rc<RefCell<bool>> = Rc::default();
    let direct_fmt = target.label.clone();
    let label = spec
        .fmt
        .clone()
        .unwrap_or_else(|| spec.to.first().cloned().unwrap_or_default());

    let direct_fmt_write = direct_fmt.clone();
    let write = {
        let report = Rc::clone(&report);
        let failed = Rc::clone(&failed);
        let notes = Rc::clone(&notes);
        let spec = spec.clone();
        let warned_to = Rc::new(RefCell::new(false));
        let warned_native = Rc::new(RefCell::new(false));
        let write: WriteFn = Box::new(move |theme, path, values| {
            let slots = to_slotmap(values);
            if theme.is_none() {
                // Legacy direct mode: the config is the truth.
                if !spec.to.is_empty() && !spec.no_push && !*warned_to.borrow() {
                    *warned_to.borrow_mut() = true;
                    notes.borrow_mut().push(
                        "--to has no push target in a direct session - \
                         the config itself is written"
                            .to_owned(),
                    );
                }
                if spec.ghostty_native && !*warned_native.borrow() {
                    *warned_native.borrow_mut() = true;
                    notes.borrow_mut().push(
                        "--ghostty-native needs a theme to name a file after - \
                         a direct session writes the config (N saves the buffer as a theme)"
                            .to_owned(),
                    );
                }
                return RealBackend::write(&direct_fmt_write, Path::new(path), &slots)
                    .map(|()| WriteOutcome::ok())
                    .map_err(|error| error.to_string());
            }
            let theme = theme.unwrap_or("");
            themes::save(theme, &slots, None).map_err(|error| error.0)?;
            report.borrow_mut().clear();
            *failed.borrow_mut() = false;
            if spec.no_push {
                report.borrow_mut().push(NO_PUSH_LINE.to_owned());
                return Ok(WriteOutcome::status(&format!("saved {theme} (truth only)")));
            }
            let result = {
                // The TUI asks through its own suspending prompter — the
                // prompt seam, same as hex and name entry — and only when
                // stdin is a real tty, so a headless driver never blocks.
                // Ctrl+A previews report-only: the saver alone offers.
                let mut ask_insert = crate::app::suspending_prompter();
                let mut offer = |label: &str| -> bool {
                    if !std::io::stdin().is_terminal() {
                        return false;
                    }
                    is_yes(ask_insert(label).as_deref().unwrap_or(""))
                };
                themes::push_confirm::<RealBackend, themes::SystemReloader>(
                    &slots,
                    &push_options(&spec, Some(theme)),
                    &themes::SystemReloader,
                    Path::new(themes::PROC_DEFAULT),
                    Some(&mut offer),
                )
                .map_err(|error| error.0)?
            };
            report.borrow_mut().extend(push_lines(&result));
            if result.failed {
                *failed.borrow_mut() = true;
                return Ok(WriteOutcome::status(&format!(
                    "saved {theme} - push failed"
                )));
            }
            Ok(WriteOutcome {
                status: format!("saved {theme} → {}", pushed_names(&result)),
                overrides: result
                    .overrides
                    .map(|(main, keys)| crate::editor::OverridesReport {
                        main: main.to_string_lossy().into_owned(),
                        keys,
                    }),
            })
        });
        write
    };

    let apply = {
        let report = Rc::clone(&report);
        let failed = Rc::clone(&failed);
        let spec = spec.clone();
        let apply: ApplyFn = Box::new(move |theme, path, values| {
            let slots = to_slotmap(values);
            if theme.is_none() {
                // Belt and suspenders: the editor routes direct-mode
                // Ctrl+A to `write`, so this should not fire — but if it
                // does, save like `write` does (the config is the truth),
                // never with a filename as the format.
                return RealBackend::write(&direct_fmt, Path::new(path), &slots)
                    .map(|()| WriteOutcome::ok())
                    .map_err(|error| error.to_string());
            }
            let theme = theme.unwrap_or("");
            report.borrow_mut().clear();
            *failed.borrow_mut() = false;
            if spec.no_push {
                report.borrow_mut().push(NO_PUSH_LINE.to_owned());
                return Ok(WriteOutcome::status(
                    "apply blocked (--no-push) - terminal unchanged",
                ));
            }
            let result = themes::push::<RealBackend>(&slots, &push_options(&spec, Some(theme)))
                .map_err(|error| error.0)?;
            report.borrow_mut().extend(push_lines(&result));
            if result.failed {
                *failed.borrow_mut() = true;
                return Ok(WriteOutcome::status("applied (unsaved) - push failed"));
            }
            Ok(WriteOutcome {
                status: format!("applied (unsaved) → {}", pushed_names(&result)),
                overrides: result
                    .overrides
                    .map(|(main, keys)| crate::editor::OverridesReport {
                        main: main.to_string_lossy().into_owned(),
                        keys,
                    }),
            })
        });
        apply
    };

    let delete_overrides = {
        let report = Rc::clone(&report);
        let spec = spec.clone();
        let delete: DeleteFn = Box::new(move |main: &str| {
            match themes::clear_ghostty_overrides::<RealBackend>(Some(Path::new(main))) {
                Ok(0) => Ok(format!("no overrides left in {main}")),
                Ok(removed) => {
                    report.borrow_mut().push(format!(
                        "ghostty: removed {removed} override(s) from {main}"
                    ));
                    if spec.reload {
                        let note = themes::reload_terminal("ghostty");
                        if !note.is_empty() {
                            report.borrow_mut().push(note);
                        }
                    }
                    Ok(format!(
                        "removed {removed} override(s) from {main} - theme now shows"
                    ))
                }
                Err(error) => Err(error.to_string()),
            }
        });
        delete
    };

    let prompt_hex: PromptFn = crate::app::suspending_prompter();
    let prompt_name: PromptFn = crate::app::suspending_prompter();

    let mut state = EditorState::new(to_slots(&target.slots), write);
    state.apply = Some(apply);
    state.delete_overrides = Some(delete_overrides);
    state.backup = Some(backup_fn());
    state.prompt_hex = Some(prompt_hex);
    state.prompt_name = Some(prompt_name);
    state.theme = target.theme.clone();
    state.fmt = label;
    state.path = target.path.to_string_lossy().into_owned();
    state.library = Some(Box::new(CliLibrary::shared(&notes)));
    if target.theme.is_none() {
        state.backup_path = Some(target.path.to_string_lossy().into_owned());
    }
    // An empty library opens on the first-run choice instead of an
    // editor with nothing to save to — chosen before a colour key fires.
    enter_setup(&mut state);

    let order: Vec<String> = providers::PROVIDER_NAMES
        .iter()
        .map(|name| name.to_string())
        .collect();
    let import = crate::editor::ImportState::new(
        order,
        Some(Box::new(CliImportLibrary::shared(&notes)) as Box<dyn ImportLibrary>),
    );
    let shell = Shell::new(state, import, Some(import_writer()));

    let driver = driver.unwrap_or_else(|| Box::new(crate::app::run));
    let shell = match driver(shell) {
        Ok(shell) => shell,
        Err(error) => return fail(err, error.to_string()),
    };
    let session = report_session(&shell.state, &report.borrow(), &notes.borrow());
    for line in &session.stdout {
        let _ = writeln!(out, "{line}");
    }
    for line in &session.stderr {
        let _ = writeln!(err, "{line}");
    }
    if *failed.borrow() {
        1
    } else {
        0
    }
}

// ---------------------------------------------------------------------------
// commands
// ---------------------------------------------------------------------------

/// `show` / `edit` / `--dump` — a theme when named, else the config.
#[allow(clippy::too_many_arguments)]
fn cmd_view(
    cli: &Cli,
    name: Option<&str>,
    action: &str,
    spec: &PushSpec,
    follow_current: bool,
    out: &mut dyn Write,
    err: &mut dyn Write,
    tty: bool,
    driver: Option<Driver>,
) -> i32 {
    let target = if let Some(name) = name {
        theme_target(name, err)
    } else if action == "edit" || follow_current {
        edit_target(cli, None, err)
    } else {
        terminal_target(cli.format.as_deref(), cli.config.as_deref(), err)
    };
    let Some(target) = target else { return 1 };

    if cli.dump {
        let _ = writeln!(out, "# {} {}", target.label, target.path.display());
        for slot in SLOTS {
            if let Some(value) = target.slots.get(slot) {
                let _ = writeln!(out, "{slot}={value}");
            }
        }
        return 0;
    }

    if action == "edit" {
        return run_editor(&target, spec, out, err, tty, driver);
    }
    let cols = term_width(tty);
    let _ = writeln!(
        out,
        "{}",
        static_preview(&target.label, &target.path, &target.slots, cols)
    );
    0
}

/// Live terminal width from `COLUMNS`, or the piped default.
fn term_width(tty: bool) -> usize {
    if !tty {
        return 96;
    }
    std::env::var("COLUMNS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(80)
}

/// `new <name>`: seed from the terminal if it has colours, else the ramp.
fn cmd_new(
    cli: &Cli,
    name: Option<&str>,
    spec: &PushSpec,
    out: &mut dyn Write,
    err: &mut dyn Write,
    tty: bool,
    driver: Option<Driver>,
) -> i32 {
    if let Some(problem) = name_problem(name, "new") {
        return fail(err, problem);
    }
    let name = name.unwrap_or("");

    let from = cli.from_fmt.as_deref().or(cli.format.as_deref());
    let (fmt, path, error) = resolve_trio(from, cli.config.as_deref());
    let explicit = cli.from_fmt.is_some() || cli.format.is_some() || cli.config.is_some();
    let found = match (&fmt, &path) {
        (Some(fmt), Some(path)) => RealBackend::read(fmt, path),
        _ => SlotMap::new(),
    };
    let (slots, source) = if !found.is_empty() {
        let source = format!(
            "{}:{}",
            fmt.clone().unwrap_or_default(),
            path.clone()
                .map(|path| path.display().to_string())
                .unwrap_or_default()
        );
        (found, Some(source))
    } else {
        if explicit {
            let reason = error
                .or_else(|| path.map(|path| format!("no colours in {}", path.display())))
                .unwrap_or_else(|| "nothing readable".to_owned());
            warn(err, &format!("{reason} - seeding from the ramp"));
        }
        (themes::ramp_slots(), None)
    };

    if let Err(error) = themes::create(name, &slots, source.as_deref(), cli.force) {
        return fail(err, error.0);
    }
    if let Err(error) = themes::set_current(name) {
        return fail(err, format!("cannot write state: {}", error.0));
    }
    let seeded = match &source {
        Some(source) => format!("seeded from {source}"),
        None => "seeded from the built-in ramp".to_owned(),
    };
    // Open the file just written, so gaps a sparse config left are
    // editable greys rather than absent slots.
    let (loaded, _) = match themes::load(name) {
        Ok(loaded) => loaded,
        Err(error) => return fail(err, error.0),
    };
    let theme_path = themes::theme_path(name).unwrap_or_else(|_| PathBuf::from(name));
    let status = run_editor(
        &Target {
            theme: Some(name.to_owned()),
            label: format!("theme {name}"),
            path: theme_path.clone(),
            slots: loaded,
        },
        spec,
        out,
        err,
        tty,
        driver,
    );
    let _ = writeln!(out, "  new theme {name}  {}", theme_path.display());
    let _ = writeln!(out, "  {seeded}\n");
    status
}

/// `import <name>`: snapshot the detected terminal. Current untouched.
fn cmd_import(cli: &Cli, name: Option<&str>, out: &mut dyn Write, err: &mut dyn Write) -> i32 {
    if let Some(problem) = name_problem(name, "import") {
        return fail(err, problem);
    }
    let name = name.unwrap_or("");

    let (fmt, path, error) = resolve_trio(cli.from_fmt.as_deref(), cli.config.as_deref());
    let (fmt, path) = match (fmt, path, error) {
        (Some(fmt), Some(path), _) => (fmt, path),
        (_, _, Some(error)) => return fail(err, error),
        _ => return fail(err, "no colours found".to_owned()),
    };
    let slots = RealBackend::read(&fmt, &path);
    if slots.is_empty() {
        return fail(err, format!("no colours found in {}", path.display()));
    }

    let source = format!("{}:{}", fmt, path.display());
    if let Err(error) = themes::create(name, &slots, Some(&source), cli.force) {
        return fail(err, error.0);
    }
    let theme_path = themes::theme_path(name).unwrap_or_else(|_| PathBuf::from(name));
    let _ = writeln!(out, "  imported {name}  {}", theme_path.display());
    let _ = writeln!(out, "  from {}:{}\n", fmt, path.display());
    0
}

/// `use <name>`: make it current, then push it live.
///
/// `tty` gates the insert offer (decision 57): on a terminal a push that
/// would report missing keys asks once whether to insert them, off one
/// the report-only behaviour stands — and a non-tty stdin never asks even
/// when the session is otherwise interactive, so a piped answer can never
/// hang the command.
fn cmd_use(
    _cli: &Cli,
    name: Option<&str>,
    spec: &PushSpec,
    out: &mut dyn Write,
    err: &mut dyn Write,
    tty: bool,
) -> i32 {
    if let Some(problem) = name_problem(name, "use") {
        return fail(err, problem);
    }
    let name = name.unwrap_or("");
    let exists = themes::theme_path(name)
        .map(|path| path.is_file())
        .unwrap_or(false);
    if !exists {
        return fail(err, format!("no such theme: {name}"));
    }

    if let Err(error) = themes::set_current(name) {
        return fail(err, format!("cannot write state: {}", error.0));
    }
    let _ = writeln!(out, "  current theme: {name}");
    let _ = out.flush();
    if spec.no_push {
        warn(err, NO_PUSH_LINE);
        return 0;
    }

    let (slots, warnings) = match themes::load(name) {
        Ok(loaded) => loaded,
        Err(error) => return fail(err, error.0),
    };
    for warning in &warnings {
        warn(err, warning);
    }
    let result = {
        // The prompt goes to stderr, where the push report already lives;
        // stdout stays script-clean. Borrowed by the closure for the call
        // only, so the report loop below reuses `err` afterwards.
        let mut ask_insert = |label: &str| -> bool {
            if !std::io::stdin().is_terminal() {
                return false;
            }
            match crate::app::read_prompt_line(label, &mut std::io::stdin().lock(), &mut *err) {
                Some(answer) => is_yes(&answer),
                None => false,
            }
        };
        let confirm: Option<&mut dyn FnMut(&str) -> bool> =
            if tty { Some(&mut ask_insert) } else { None };
        match themes::push_confirm::<RealBackend, themes::SystemReloader>(
            &slots,
            &push_options(spec, Some(name)),
            &themes::SystemReloader,
            Path::new(themes::PROC_DEFAULT),
            confirm,
        ) {
            Ok(result) => result,
            Err(error) => return fail(err, error.0),
        }
    };
    for line in push_lines(&result) {
        warn(err, &line);
    }
    if result.failed {
        1
    } else {
        0
    }
}

/// `list`: one line per theme, current marked, source and age shown.
fn cmd_list(out: &mut dyn Write, porcelain: bool) -> i32 {
    let rows = themes::list_themes();
    if rows.is_empty() {
        // Porcelain prints data lines only: no hint, no header.
        if !porcelain {
            let _ = writeln!(out, "  no themes yet - create one with: huebox new <name>");
        }
        return 0;
    }
    // Machine shape (decision 58): `name \t path \t mtime-epoch`, same
    // order as the human list (`list_themes` sorts by name).
    if porcelain {
        for (name, path, mtime) in &rows {
            let _ = writeln!(out, "{}\t{}\t{}", name, path.display(), *mtime as i64);
        }
        return 0;
    }
    let current = themes::current();
    let width = rows
        .iter()
        .map(|(name, _, _)| name.len())
        .max()
        .unwrap_or(0);
    for (name, _, mtime) in &rows {
        let origin = themes::source_of(name)
            .unwrap_or_default()
            .split(':')
            .next()
            .unwrap_or("")
            .to_owned();
        let origin = if origin.is_empty() { "-" } else { &origin };
        let stamp = local_stamp(*mtime);
        let mark = if Some(name) == current.as_ref() {
            "*"
        } else {
            " "
        };
        let _ = writeln!(out, "{mark} {name:<width$}  {origin:<8}  {stamp}");
    }
    0
}

/// `YYYY-MM-DD HH:MM` for an epoch-seconds mtime, local time like Python's
/// `datetime.fromtimestamp` — the `list` stamp a hand-read compares.
fn local_stamp(secs: f64) -> String {
    chrono::DateTime::from_timestamp(secs as i64, 0)
        .map(|utc| {
            utc.with_timezone(&chrono::Local)
                .format("%Y-%m-%d %H:%M")
                .to_string()
        })
        .unwrap_or_else(|| "1970-01-01 00:00".to_owned())
}

// ---------------------------------------------------------------------------
// static preview (`show`): plain-text shape of `render_preview`
// ---------------------------------------------------------------------------

/// The non-interactive preview: subject line, palette grid, named slots,
/// and the edit hint.
///
/// Shape follows `render_preview` (grid choice, labels, hint) with ANSI
/// swatches; the raster/mini banner art is omitted (a text header stands
/// in — full art parity belongs to the pty harness phase).
pub fn static_preview(label: &str, path: &Path, slots: &SlotMap, cols: usize) -> String {
    let cols = cols.max(20);
    let mut lines: Vec<String> = Vec::new();
    // Live buffer for the logo ladder: canonical slots only, like the
    // editor buffer `render_preview` reads.
    let mut core = Slots::default();
    for name in SLOTS {
        if let Some(value) = slots.get(name) {
            core.set(name, value);
        }
    }
    // The logo ladder from `render_preview` (§8.1): raster banner first,
    // mini below that, the one-line wordmark when narrower than either.
    let art = banner::banner_lines(&core, Some(cols), None);
    let mark = banner::mini_banner_lines(&core, Some(cols));
    let logo = if !art.is_empty() { art } else { mark };
    if logo.is_empty() {
        let mut subject = paint_ansi(&chrome::wordmark_default(&core));
        subject.push_str("  ");
        subject.push_str(&paint_ansi(&chrome::title(label, &core, "")));
        lines.push(subject);
    } else {
        for row in &logo {
            lines.push(paint_ansi(row));
        }
        lines.push(paint_ansi(&chrome::title(label, &core, "")));
    }
    let shown = path.display().to_string();
    if !shown.is_empty() && shown.len() < cols {
        let last = lines.len() - 1;
        lines[last].push_str("  ");
        lines[last].push_str(&paint_ansi(&chrome::chrome(
            &shown,
            crate::render::chrome::CHROME_MUTED,
            &core,
        )));
    }
    lines.push(String::new());

    let indent = "  ";
    let mut choice = (1, 13);
    for option in [(8, 13), (8, 6), (4, 13), (4, 6), (2, 13), (1, 13)] {
        if indent.len() + option.0 * option.1 <= cols {
            choice = option;
            break;
        }
    }
    let (per_row, cellw) = choice;
    let show_hex = cellw >= 13;
    for start in (0..16).step_by(per_row) {
        let mut row = indent.to_owned();
        for i in start..(start + per_row).min(16) {
            let value = slots
                .get(&format!("palette-{i}"))
                .cloned()
                .unwrap_or_else(|| crate::library::MISSING.to_owned());
            let label = if show_hex {
                format!(" {i:>2} {value} ")
            } else {
                format!(" {i:>2}")
            };
            row.push_str(&swatch(&label, &value, cellw));
        }
        lines.push(clip(&rstrip_ansi(row), cols));
    }
    lines.push(paint_ansi(&chrome::chrome(
        "0-7 base   8-15 bright",
        crate::render::chrome::CHROME_MUTED,
        &core,
    )));
    lines.push(String::new());

    let pad = 8.max(21.min(cols.saturating_sub(13)));
    let iface = pad + 11;
    let per = if indent.len() + 2 * iface + 2 <= cols {
        2
    } else {
        1
    };
    let named = [
        "background",
        "foreground",
        "cursor-color",
        "cursor-text",
        "selection-background",
        "selection-foreground",
    ];
    for chunk in named.chunks(per) {
        let mut row = indent.to_owned();
        for (i, key) in chunk.iter().enumerate() {
            if i > 0 {
                row.push_str("  ");
            }
            let value = slots
                .get(*key)
                .cloned()
                .unwrap_or_else(|| crate::library::MISSING.to_owned());
            // No width pad: Python's named cells are never padded, and
            // the row is rstripped below.
            let text = format!(" {key:<pad$} {value} ");
            let width = text.chars().count();
            row.push_str(&swatch(&text, &value, width));
        }
        lines.push(clip(&rstrip_ansi(row), cols));
    }
    lines.push(String::new());
    let mut hint = "  ".to_owned();
    hint.push_str(&paint_ansi(&chrome::chrome(
        "edit interactively:  huebox edit",
        crate::render::chrome::CHROME_MUTED,
        &core,
    )));
    lines.push(hint);
    lines.push(String::new());
    lines.join("\n")
}

/// One painted row as ANSI: consecutive cells sharing `(fg, bg, bold)`
/// form one run (`\x1b[1m`? + fg + bg + text + reset), exactly the runs
/// Python's banner painter emits. Trailing air is stripped like the
/// frame's own rows.
pub fn paint_ansi(row: &Row) -> String {
    let mut out = String::new();
    let mut run = String::new();
    let mut key: Option<(String, String, bool)> = None;
    let flush = |out: &mut String, run: &mut String, key: &Option<(String, String, bool)>| {
        if run.is_empty() {
            return;
        }
        let (fg, bg, bold) = key.clone().unwrap_or_default();
        // Python's exact run shape: `BOLD?` + fg + bg + text + reset as
        // SEPARATE escapes (`\x1b[1m\x1b[38;2;..m`), never combined.
        let mut codes = String::new();
        if bold {
            codes.push_str("\x1b[1m");
        }
        if !fg.is_empty() {
            let (r, g, b) = rgb(&fg);
            codes.push_str(&format!("\x1b[38;2;{r};{g};{b}m"));
        }
        if !bg.is_empty() {
            let (r, g, b) = rgb(&bg);
            codes.push_str(&format!("\x1b[48;2;{r};{g};{b}m"));
        }
        if codes.is_empty() {
            out.push_str(run);
        } else {
            out.push_str(&format!("{codes}{run}\x1b[0m"));
        }
        run.clear();
    };
    for cell in row {
        let cell_key = (cell.fg.clone(), cell.bg.clone(), cell.bold);
        if key.as_ref() != Some(&cell_key) {
            flush(&mut out, &mut run, &key);
            key = Some(cell_key);
        }
        run.push(cell.ch);
    }
    flush(&mut out, &mut run, &key);
    // Strip trailing air like the frame rows, keeping the reset.
    while out.ends_with(' ') {
        out.pop();
    }
    out
}

/// One swatch cell: `text` on `hex`, padded to `width` display columns.
///
/// The pad rides INSIDE the painted span like Python's `label.ljust`, so
/// the colour field grows instead of leaking terminal-background air.
fn swatch(text: &str, hex: &str, width: usize) -> String {
    let (br, bg, bb) = rgb(hex);
    let (fr, fg, fb) = rgb(crate::core::readable_fg(hex));
    let mut padded = text.to_owned();
    let visible = text.chars().count();
    if visible < width {
        padded.push_str(&" ".repeat(width - visible));
    }
    format!("\x1b[48;2;{br};{bg};{bb}m\x1b[38;2;{fr};{fg};{fb}m{padded}\x1b[0m")
}

/// Trailing air off an ANSI line: spaces past the last reset go, exactly
/// Python's `.rstrip()` on the joined row (escapes end in `m`, so only
/// real air is ever stripped).
fn rstrip_ansi(mut line: String) -> String {
    while line.ends_with(' ') {
        line.pop();
    }
    line
}

/// `hex` to 0–255 channels (unparseable reads black, never panics).
fn rgb(hex: &str) -> (u8, u8, u8) {
    let body = hex.trim().trim_start_matches('#');
    if body.len() == 6 && body.bytes().all(|b| b.is_ascii_hexdigit()) {
        let channel = |at: usize| u8::from_str_radix(&body[at..at + 2], 16).unwrap_or(0);
        (channel(0), channel(2), channel(4))
    } else {
        (0, 0, 0)
    }
}

/// First `cols` display columns (ANSI escapes are not columns).
fn clip(line: &str, cols: usize) -> String {
    let mut out = String::new();
    let mut width = 0;
    let mut chars = line.chars().peekable();
    while let Some(char) = chars.next() {
        if char == '\x1b' {
            // Keep the whole escape sequence; it paints, not measures.
            out.push(char);
            for char in chars.by_ref() {
                out.push(char);
                if char == 'm' {
                    break;
                }
            }
            continue;
        }
        if width >= cols {
            // Past the edge: visible columns are dropped, but escape
            // sequences still pass through so the reset is not lost.
            if char != '\x1b' {
                continue;
            }
        } else {
            width += 1;
        }
        out.push(char);
    }
    out
}

/// Format registry names in canonical order (clap clamps `--format` /
/// `--from` to these before any lookup, which would panic on unknown).
pub fn format_names() -> &'static [&'static str] {
    RealBackend::format_names()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn push_lines_owes_no_hint_to_live_reloaders() {
        let bare = |fmt: &str| themes::PushResult {
            lines: Vec::new(),
            pushed: vec![(fmt.to_owned(), PathBuf::from("/tmp/x"))],
            failed: false,
            reloaded: Vec::new(),
            overrides: None,
        };
        // Decision 53: an alacritty-only push is already live — no hint.
        assert!(push_lines(&bare("alacritty")).is_empty());
        // Anything else unreloaded still owes it.
        assert_eq!(push_lines(&bare("ghostty")), vec![RELOAD_HINT.to_owned()]);
    }

    #[test]
    fn list_porcelain_prints_name_path_epoch() {
        use crate::library::testutil::{lock_env, temp_dir};
        let home = temp_dir("cli-porcelain");
        let root = home.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str()), ("HOME", root.as_str())]);
        home.write("huebox/themes/ember.toml", "background = \"#101014\"\n");
        home.write("huebox/themes/dusk.toml", "background = \"#0a0a13\"\n");
        let mut out = Vec::new();
        let mut err = Vec::new();
        let code = run(
            vec!["huebox".into(), "list".into(), "--porcelain".into()],
            &mut out,
            &mut err,
            false,
            None,
        );
        assert_eq!(code, 0);
        assert!(err.is_empty());
        let text = String::from_utf8(out).unwrap();
        let mut rows = text.lines();
        // Sorted by name, `name \t path \t mtime-epoch`, no hint line.
        let first = rows.next().unwrap();
        let parts: Vec<&str> = first.split('\t').collect();
        assert_eq!(parts.len(), 3);
        assert_eq!(parts[0], "dusk");
        assert!(parts[1].ends_with("huebox/themes/dusk.toml"));
        assert!(parts[2].parse::<i64>().is_ok());
        let second = rows.next().unwrap();
        assert!(second.starts_with("ember\t"));
        assert!(rows.next().is_none());
        // Epoch correctness, not just shape: the third field is the
        // file's own mtime (tolerance covers a second boundary).
        let ember = home.path().join("huebox/themes/ember.toml");
        let actual = std::fs::metadata(&ember)
            .unwrap()
            .modified()
            .unwrap()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;
        let printed: i64 = second.split('\t').nth(2).unwrap().parse().unwrap();
        assert!((printed - actual).abs() <= 2);
    }

    #[test]
    fn porcelain_outside_list_is_a_usage_error() {
        let mut out = Vec::new();
        let mut err = Vec::new();
        let code = run(
            vec![
                "huebox".into(),
                "use".into(),
                "ember".into(),
                "--porcelain".into(),
            ],
            &mut out,
            &mut err,
            false,
            None,
        );
        assert_eq!(code, 2);
        assert!(String::from_utf8(err)
            .unwrap()
            .contains("--porcelain needs list"));
    }

    #[test]
    fn list_porcelain_empty_library_prints_nothing() {
        use crate::library::testutil::{lock_env, temp_dir};
        let home = temp_dir("cli-porcelain-empty");
        let root = home.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str()), ("HOME", root.as_str())]);
        let mut out = Vec::new();
        let mut err = Vec::new();
        let code = run(
            vec!["huebox".into(), "list".into(), "--porcelain".into()],
            &mut out,
            &mut err,
            false,
            None,
        );
        assert_eq!(code, 0);
        assert!(out.is_empty());
        assert!(err.is_empty());
    }

    #[test]
    fn porcelain_with_formats_is_rejected() {
        let mut out = Vec::new();
        let mut err = Vec::new();
        let code = run(
            vec![
                "huebox".into(),
                "list".into(),
                "--formats".into(),
                "--porcelain".into(),
            ],
            &mut out,
            &mut err,
            false,
            None,
        );
        assert_eq!(code, 2);
        assert!(String::from_utf8(err)
            .unwrap()
            .contains("--porcelain cannot be combined with --formats"));
    }

    fn cli(argv: &[&str]) -> Cli {
        let owned: Vec<String> = argv.iter().map(|arg| arg.to_string()).collect();
        Cli::try_parse_from(&owned).expect("test argv parses")
    }

    #[test]
    fn push_spec_defaults_match_python() {
        let spec = push_spec(&cli(&["huebox"])).expect("defaults");
        assert!(spec.to.is_empty());
        assert!(spec.fmt.is_none());
        assert!(spec.path.is_none());
        assert!(!spec.no_push);
        assert!(!spec.ghostty_native);
        assert!(!spec.ghostty_in_place);
        assert!(spec.reload);
    }

    #[test]
    fn push_spec_rejects_opposites_and_empty_to() {
        let error = push_spec(&cli(&["huebox", "--ghostty-native", "--ghostty-in-place"]))
            .expect_err("opposites");
        assert!(error.contains("opposite"), "{error}");

        let error = push_spec(&cli(&["huebox", "--to", " , "])).expect_err("empty --to");
        assert_eq!(error, "--to needs at least one format");

        let error = push_spec(&cli(&["huebox", "--to", "wezterm"])).expect_err("unknown --to");
        assert!(error.contains("unknown format 'wezterm'"), "{error}");
        assert!(error.contains("ghostty, kitty, alacritty"), "{error}");
    }

    #[test]
    fn push_spec_dedups_and_trims_to() {
        let spec = push_spec(&cli(&["huebox", "--to", " kitty,ghostty,kitty "])).expect("to");
        assert_eq!(spec.to, vec!["kitty".to_owned(), "ghostty".to_owned()]);
    }

    #[test]
    fn name_problems_match_python() {
        assert_eq!(
            name_problem(None, "new"),
            Some("new needs a theme name".to_owned())
        );
        assert_eq!(
            name_problem(Some("-bad"), "use"),
            Some("invalid theme name".to_owned())
        );
        assert_eq!(name_problem(Some("ember"), "use"), None);
    }

    #[test]
    fn push_report_advice_matches_python() {
        let mut result = themes::PushResult::empty();
        assert!(push_lines(&result).is_empty());
        result
            .pushed
            .push(("kitty".to_owned(), PathBuf::from("/tmp/kitty.conf")));
        assert_eq!(push_lines(&result), vec![RELOAD_HINT.to_owned()]);
        assert_eq!(pushed_names(&result), "kitty");
    }

    #[test]
    fn preview_grid_collapses_with_width() {
        let slots: SlotMap = SLOTS
            .iter()
            .map(|slot| (slot.to_string(), "#101014".to_owned()))
            .collect();
        let wide = static_preview("ghostty", Path::new("/tmp/ghostty"), &slots, 96);
        let narrow = static_preview("ghostty", Path::new("/tmp/ghostty"), &slots, 30);
        assert!(wide.contains("0-7 base   8-15 bright"));
        assert!(wide.contains("edit interactively:  huebox edit"));
        // Narrow lays fewer palette cells per row, so more rows.
        assert!(narrow.lines().count() > wide.lines().count());
    }

    #[test]
    fn clip_keeps_escapes_but_counts_columns() {
        let clipped = clip("\x1b[48;2;1;2;3mhello world\x1b[0m", 5);
        assert!(clipped.contains("hello"));
        assert!(!clipped.contains("world"));
        assert!(clipped.contains("\x1b[0m"));
    }
}
