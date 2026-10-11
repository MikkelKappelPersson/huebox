//! CLI gates: surface parity, exit codes, and headless seam wiring.
//!
//! Env override used: `HOME` + `XDG_CONFIG_HOME` point at a tempdir per
//! test (serialised by `ENV_LOCK` — the lib reads process env at call
//! time, so parallel tests must not race it). `GHOSTTY_RESOURCES_DIR`
//! points at a missing dir so provider scans stay hermetic.

use std::io::Write;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use clap::{CommandFactory, Parser};
use huebox_cli::{run, Cli};
use huebox_editor::apply_key;

static ENV_LOCK: Mutex<()> = Mutex::new(());

struct Env {
    _guard: MutexGuard<'static, ()>,
    root: PathBuf,
    saved: Vec<(String, Option<String>)>,
}

impl Env {
    fn setup() -> Self {
        let guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
        let root = std::env::temp_dir().join(format!("huebox-cli-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let mut saved = Vec::new();
        for (key, value) in [
            ("HOME", root.to_string_lossy().into_owned()),
            ("XDG_CONFIG_HOME", root.to_string_lossy().into_owned()),
            (
                "GHOSTTY_RESOURCES_DIR",
                root.join("no-shipped").to_string_lossy().into_owned(),
            ),
            ("COLUMNS", "96".to_owned()),
        ] {
            saved.push((key.to_owned(), std::env::var(key).ok()));
            // `set_var` is the hermetic-HOME seam, not production code.
            unsafe { std::env::set_var(key, &value) };
        }
        Self {
            _guard: unsafe {
                std::mem::transmute::<MutexGuard<'_, ()>, MutexGuard<'static, ()>>(guard)
            },
            root,
            saved,
        }
    }

    fn run(
        &self,
        argv: &[&str],
        tty: bool,
        driver: Option<huebox_cli::Driver>,
    ) -> (i32, String, String) {
        let owned: Vec<String> = argv.iter().map(|arg| arg.to_string()).collect();
        let mut out = Vec::new();
        let mut err = Vec::new();
        let code = run(owned, &mut out, &mut err, tty, driver);
        (
            code,
            String::from_utf8_lossy(&out).into_owned(),
            String::from_utf8_lossy(&err).into_owned(),
        )
    }

    /// A driver that saves then quits, headless: the seam-wiring proof.
    fn save_and_quit(&self, argv: &[&str]) -> (i32, String, String) {
        self.run(
            argv,
            true,
            Some(Box::new(|mut shell: huebox_app::Shell| {
                apply_key("\x13", &mut shell.state);
                apply_key("esc", &mut shell.state);
                assert!(shell.state.quit, "clean Esc quits the session");
                Ok(shell)
            })),
        )
    }
}

impl Drop for Env {
    fn drop(&mut self) {
        for (key, value) in std::mem::take(&mut self.saved) {
            unsafe {
                match value {
                    Some(value) => std::env::set_var(&key, value),
                    None => std::env::remove_var(&key),
                }
            }
        }
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

fn parse(argv: &[&str]) {
    let owned: Vec<String> = argv.iter().map(|arg| arg.to_string()).collect();
    match Cli::try_parse_from(&owned) {
        Ok(_) => {}
        // `--help` / `--version` short-circuit as display "errors".
        Err(error)
            if matches!(
                error.kind(),
                clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion
            ) => {}
        Err(error) => panic!("parses: {argv:?}: {error}"),
    }
}

// ---------------------------------------------------------------------------
// surface: every command and flag parses; --help lists the same commands
// ---------------------------------------------------------------------------

#[test]
fn every_command_and_flag_parses() {
    let _env = Env::setup();
    for argv in [
        vec!["huebox"],
        vec!["huebox", "show"],
        vec!["huebox", "show", "ember"],
        vec!["huebox", "edit"],
        vec!["huebox", "edit", "ember"],
        vec!["huebox", "new", "ember"],
        vec!["huebox", "list"],
        vec!["huebox", "use", "ember"],
        vec!["huebox", "import", "ember"],
        vec!["huebox", "--dump"],
        vec!["huebox", "--dump", "ember"],
        vec!["huebox", "ember"],
        vec!["huebox", "--formats"],
        vec!["huebox", "--version"],
        vec!["huebox", "-f", "ghostty", "show"],
        vec!["huebox", "--format", "kitty", "show"],
        vec!["huebox", "-c", "/tmp/kitty.conf", "show"],
        vec!["huebox", "--config", "/tmp/kitty.conf", "show"],
        vec!["huebox", "new", "ember", "--force"],
        vec!["huebox", "import", "ember", "--from", "kitty"],
        vec![
            "huebox",
            "import",
            "ember",
            "--from",
            "alacritty",
            "--force",
        ],
        vec!["huebox", "use", "ember", "--to", "ghostty,kitty"],
        vec!["huebox", "use", "ember", "--no-push"],
        vec!["huebox", "use", "ember", "--ghostty-native"],
        vec!["huebox", "use", "ember", "--ghostty-in-place"],
        vec!["huebox", "use", "ember", "--no-reload"],
        vec!["huebox", "--dump", "show"],
    ] {
        parse(&argv);
    }
}

#[test]
fn help_lists_the_same_commands_and_flags() {
    let mut help = Vec::new();
    Cli::command().write_long_help(&mut help).unwrap();
    let help = String::from_utf8(help).unwrap();
    for command in ["show", "edit", "new", "list", "use", "import"] {
        assert!(help.contains(command), "help lists {command}:\n{help}");
    }
    for flag in [
        "--format",
        "--config",
        "--force",
        "--from",
        "--to",
        "--no-push",
        "--ghostty-native",
        "--ghostty-in-place",
        "--no-reload",
        "--dump",
        "--formats",
        "--version",
    ] {
        assert!(help.contains(flag), "help lists {flag}:\n{help}");
    }
}

#[test]
fn formats_lists_the_registry() {
    let env = Env::setup();
    let (code, out, _) = env.run(&["huebox", "--formats"], false, None);
    assert_eq!(code, 0);
    assert_eq!(out, "ghostty\nkitty\nalacritty\n");
}

// ---------------------------------------------------------------------------
// exit codes: user errors are `huebox: ` + exit 1, never a traceback
// ---------------------------------------------------------------------------

fn assert_user_error(code: i32, err: &str) {
    assert_eq!(code, 1, "stderr was:\n{err}");
    assert!(err.starts_with("huebox: "), "prefixed:\n{err}");
    for leaked in ["Traceback", "panicked", "thread '", "backtrace", "Error: "] {
        assert!(!err.contains(leaked), "no traceback ({leaked}):\n{err}");
    }
}

#[test]
fn bad_theme_is_a_clean_exit_1() {
    let env = Env::setup();
    let (code, _, err) = env.run(&["huebox", "use", "nosuchtheme"], false, None);
    assert_user_error(code, &err);
    assert!(err.contains("no such theme: nosuchtheme"), "{err}");

    let (code, _, err) = env.run(&["huebox", "show", "nosuchtheme"], false, None);
    assert_user_error(code, &err);

    let (code, _, err) = env.run(&["huebox", "use", "ember!"], false, None);
    assert_user_error(code, &err);
    assert!(err.contains("invalid theme name"), "{err}");

    let (code, _, err) = env.run(&["huebox", "use", "ember", "--to", "wezterm"], false, None);
    assert_user_error(code, &err);
    assert!(err.contains("unknown format 'wezterm'"), "{err}");

    let (code, _, err) = env.run(&["huebox", "new"], false, None);
    assert_user_error(code, &err);
    assert!(err.contains("new needs a theme name"), "{err}");

    let (code, _, err) = env.run(&["huebox", "list", "--dump"], false, None);
    assert_eq!(code, 2, "dump combo is a usage error:\n{err}");

    let (code, _, err) = env.run(&["huebox", "frobnicate", "extra"], false, None);
    assert_eq!(code, 2, "unknown action is a usage error:\n{err}");
    assert!(err.contains("unknown action"), "{err}");
}

// ---------------------------------------------------------------------------
// seam wiring: fake driver drives the real Shell headless
// ---------------------------------------------------------------------------

#[test]
fn new_edit_list_use_dump_show_wire_end_to_end() {
    let env = Env::setup();

    // `new` seeds from the ramp (no terminal here) and opens the editor:
    // the headless driver saves (truth file lands) and quits.
    let (code, out, err) = env.save_and_quit(&["huebox", "new", "ember", "--no-push"]);
    assert_eq!(code, 0, "stdout:\n{out}\nstderr:\n{err}");
    assert!(out.contains("saved theme ember"), "{out}");
    assert!(out.contains("new theme ember"), "{out}");
    assert!(out.contains("seeded from the built-in ramp"), "{out}");
    assert!(
        env.root.join("huebox/themes/ember.toml").is_file(),
        "truth file written"
    );

    // `list` marks the new current theme.
    let (code, out, _) = env.run(&["huebox", "list"], false, None);
    assert_eq!(code, 0);
    assert!(out.contains("* ember"), "{out}");

    // `use --no-push` keeps truth-only sessions honest.
    let (code, out, err) = env.run(&["huebox", "use", "ember", "--no-push"], false, None);
    assert_eq!(code, 0, "{err}");
    assert!(out.contains("current theme: ember"), "{out}");
    assert!(err.contains("no push requested"), "{err}");

    // `--dump` prints all 22 slots as key=value.
    let (code, out, _) = env.run(&["huebox", "--dump", "ember"], false, None);
    assert_eq!(code, 0);
    assert!(out.contains("palette-0="), "{out}");
    assert!(out.contains("selection-foreground="), "{out}");
    assert_eq!(
        out.lines().filter(|line| line.contains('=')).count(),
        22,
        "{out}"
    );

    // `show` previews without touching the editor.
    let (code, out, _) = env.run(&["huebox", "show", "ember"], false, None);
    assert_eq!(code, 0);
    assert!(out.contains("edit interactively"), "{out}");

    // The picker seam sees the library the session just wrote.
    let notes = std::rc::Rc::new(std::cell::RefCell::new(Vec::<String>::new()));
    let library = huebox_cli::CliLibrary::shared(&notes);
    assert!(huebox_editor::Library::names(&library).contains(&"ember".to_owned()));
    let (slots, path) = huebox_editor::Library::load(&library, "ember").expect("loads");
    assert!(path.ends_with("ember.toml"), "{path}");
    assert_eq!(slots.get("background"), "#101014", "ramp seed round-trips");
}

#[test]
fn piped_editor_reports_the_real_problem() {
    let env = Env::setup();
    // A resolvable target, so the run reaches the tty check (target
    // resolution runs first, exactly like cli.py).
    let (code, _, _) = env.save_and_quit(&["huebox", "new", "ember", "--no-push"]);
    assert_eq!(code, 0);
    // cli.py returns 0 here and spec §2 pins identical exit codes.
    let (code, _, err) = env.run(&["huebox", "edit", "ember"], false, None);
    assert_eq!(code, 0);
    assert!(err.contains("not a terminal"), "{err}");
    assert!(err.starts_with("huebox: "), "{err}");
}

#[test]
fn import_writer_lands_truth_only() {
    let _env = Env::setup();
    let mut writer = huebox_cli::import_writer();
    let mut slots = huebox_core::Slots::default();
    slots.set("background", "#000000");
    writer("snap", &slots, "ghostty:/tmp/config").expect("lands");
    let (loaded, _) = huebox_lib::themes::load("snap").expect("truth file");
    assert_eq!(
        loaded.get("background").map(String::as_str),
        Some("#000000")
    );
    // Confirming twice without force is a refusal, not a clobber.
    assert!(writer("snap", &slots, "ghostty:/tmp/config").is_err());
}

#[test]
fn import_library_lists_and_reads_through_providers() {
    let env = Env::setup();
    // A kitty theme in the user dir lists through the provider facade.
    let dir = env.root.join("kitty/themes");
    std::fs::create_dir_all(&dir).unwrap();
    let mut config = std::fs::File::create(dir.join("ember.conf")).unwrap();
    write!(
        config,
        "background #101014\nforeground #e6e6ea\ncolor0 #0a0a13\ncolor15 #f8f8ff\n"
    )
    .unwrap();
    let notes = std::rc::Rc::new(std::cell::RefCell::new(Vec::<String>::new()));
    let library = huebox_cli::CliImportLibrary::shared(&notes);
    let entries = huebox_editor::ImportLibrary::entries(&library, "kitty");
    assert!(
        entries.iter().any(|(display, _)| display == "ember"),
        "{entries:?}"
    );
    let path = entries
        .iter()
        .find(|(display, _)| display == "ember")
        .unwrap()
        .1
        .clone();
    let slots = huebox_editor::ImportLibrary::read(&library, "kitty", &path).expect("reads");
    assert_eq!(slots.get("background"), "#101014");
    assert_eq!(
        huebox_editor::ImportLibrary::format_of(&library, "kitty"),
        "kitty"
    );
    assert!(huebox_editor::ImportLibrary::entries(&library, "wezterm").is_empty());
}
