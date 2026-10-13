//! The theme library: `~/.config/huebox` is the truth.
//!
//! Rust port of `huebox/themes.py`: home / `state.toml`, theme files, the
//! canonical writer + subset reader, [`push`] (truth → terminal, the one
//! write out), Ghostty native export, reload, overrides, [`RAMP`].
//!
//! Theme files belong to huebox (canonical layout, `.tmp` + rename, never
//! partially written); terminal configs keep their line-level writer via
//! [`crate::library::format::FormatStub`] and are never re-serialised.
//!
//! A save is truth first, terminal second, never the other way round: the
//! theme file outlives any one config, so a failed push is reported and
//! left alone — nothing is ever rolled back.
//!
//! `state.toml` is parsed with the same hand-rolled subset grammar as
//! `themes.py` (sections, `key = value` quoted or bare, comments) — no
//! `serde`/`toml` dependency, matching the Python semantics exactly.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::mpsc;
use std::time::Duration;

use crate::library::detect::{
    config_holds_colours, ensure_theme_pointer, ghostty_main_config, ghostty_theme_name,
    ghostty_themes_dir, resolve_for_push,
};
use crate::library::format::{unknown_format, FormatStub};
use crate::library::{is_hex, normalize_hex, SlotMap, ThemeError, MISSING, SLOTS};

/// One name rule: `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, 64 chars at most.
/// The name is a file name, so the rule stays to what every filesystem
/// accepts without quoting.
pub fn valid_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    if bytes.is_empty() || bytes.len() > 64 {
        return false;
    }
    if !bytes[0].is_ascii_alphanumeric() {
        return false;
    }
    bytes
        .iter()
        .all(|b| b.is_ascii_alphanumeric() || *b == b'_' || *b == b'-')
}

fn check(name: &str) -> Result<(), ThemeError> {
    if !valid_name(name) {
        return Err(ThemeError("invalid theme name".to_owned()));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// where things live
// ---------------------------------------------------------------------------

/// `$XDG_CONFIG_HOME/huebox`, falling back to `~/.config/huebox`.
/// Config home, not data home: themes are hand-edited and belong in a
/// dotfiles repo next to the terminal configs they drive.
pub fn home() -> PathBuf {
    crate::library::detect::config_home().join("huebox")
}

/// The theme library directory.
pub fn themes_dir() -> PathBuf {
    home().join("themes")
}

/// The state pointer file.
pub fn state_path() -> PathBuf {
    home().join("state.toml")
}

/// The theme file for `name` (validates first).
pub fn theme_path(name: &str) -> Result<PathBuf, ThemeError> {
    check(name)?;
    Ok(themes_dir().join(format!("{name}.toml")))
}

// ---------------------------------------------------------------------------
// the TOML subset we both write and read (hand-rolled, no toml crate)
// ---------------------------------------------------------------------------

/// Suffix of every theme file.
pub const SUFFIX: &str = ".toml";
/// Meta keys kept in the `[theme]` block.
const META_KEYS: [&str; 4] = ["name", "created", "modified", "source"];
/// First line of every theme file.
pub const HEADER: &str = "# owned by huebox — values are yours, structure is ours";
/// First line of every exported Ghostty theme file.
pub const GHOSTTY_HEADER: &str = "# a huebox theme file — rewritten on every push";

fn is_hex_token_at(bytes: &[u8], at: usize) -> bool {
    // `#[0-9a-fA-F]{6}\b`: a quoted or bare `#rrggbb` is a colour, not a
    // comment opener.
    let mut i = at + 1;
    for _ in 0..6 {
        if i >= bytes.len() || !(bytes[i] as char).is_ascii_hexdigit() {
            return false;
        }
        i += 1;
    }
    i >= bytes.len() || !((bytes[i] as char).is_ascii_alphanumeric() || bytes[i] == b'_')
}

/// Drop a trailing `# …`, ignoring a `#` that is part of a value: a quoted
/// `"#1a1b26"` is a colour and not a comment, and so is a bare one.
fn strip_comment(line: &str) -> &str {
    let bytes = line.as_bytes();
    let mut i = 0;
    let mut quote: Option<u8> = None;
    while i < bytes.len() {
        let c = bytes[i];
        if let Some(q) = quote {
            if c == q {
                quote = None;
            }
            i += 1;
        } else if c == b'"' || c == b'\'' {
            quote = Some(c);
            i += 1;
        } else if c == b'#' && !is_hex_token_at(bytes, i) {
            break;
        } else {
            i += 1;
        }
    }
    line[..i].trim()
}

fn unquote(value: &str) -> &str {
    let bytes = value.as_bytes();
    if bytes.len() >= 2
        && bytes[0] == bytes[bytes.len() - 1]
        && (bytes[0] == b'"' || bytes[0] == b'\'')
    {
        &value[1..value.len() - 1]
    } else {
        value
    }
}

type Sections = BTreeMap<String, BTreeMap<String, String>>;

/// `{section: {key: raw value}}` plus the number of unparsable lines.
/// Our grammar, not TOML at large: `[section]` headers, `key = value`
/// with the value quoted or bare, comments and blank lines anywhere.
fn parse(text: &str) -> (Sections, usize) {
    let mut sections: Sections = Sections::new();
    sections.insert(String::new(), BTreeMap::new());
    let mut section = String::new();
    let mut dropped = 0;
    for raw in text.split('\n') {
        // splitlines: a trailing \r is not content.
        let raw = raw.strip_suffix('\r').unwrap_or(raw);
        let line = strip_comment(raw);
        if line.is_empty() {
            continue;
        }
        if line.starts_with('[') {
            section = unquote(line.trim().trim_matches(['[', ']']).trim()).to_owned();
            sections.entry(section.clone()).or_default();
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            dropped += 1; // no key/value at all
            continue;
        };
        let key = unquote(key.trim()).to_owned();
        let block = sections.entry(section.clone()).or_default();
        if block.contains_key(&key) {
            dropped += 1; // a duplicate: the re-save loses one
        }
        block.insert(key, unquote(value.trim()).to_owned());
    }
    (sections, dropped)
}

fn read_file(path: &Path) -> (Sections, usize) {
    match std::fs::read(path) {
        Ok(bytes) => parse(&String::from_utf8_lossy(&bytes)),
        Err(_) => (Sections::new(), 0),
    }
}

/// Local naive ISO-8601 to the second, no timezone.
///
/// Local ISO-8601 to the second, no timezone — Python's `datetime.now()`
/// verbatim via `chrono::Local`. Tests pin the clock through
/// [`save_with`] / [`create_with`], so this only runs on live paths.
pub fn system_now() -> String {
    chrono::Local::now()
        .naive_local()
        .format("%Y-%m-%dT%H:%M:%S")
        .to_string()
}

/// The value a slot is written/pushed as: normalised hex, or the same
/// `MISSING` grey the editor shows for a gap.
pub fn slot_value(slots: &SlotMap, slot: &str) -> String {
    match slots.get(slot) {
        Some(value) if !value.is_empty() && is_hex(value) => normalize_hex(value),
        _ => MISSING.to_owned(),
    }
}

/// Write a sibling tmp and rename it over the target: all-or-nothing, so
/// a crash or a full disk leaves the old file or the new one, never half
/// a theme. Never leaves a half-written file behind.
fn atomic_write(path: &Path, text: &str) -> Result<(), ThemeError> {
    let tmp = path.to_string_lossy().into_owned() + &format!(".{}.tmp", std::process::id());
    if let Err(problem) = std::fs::write(&tmp, text) {
        let _ = std::fs::remove_file(&tmp);
        return Err(ThemeError(problem.to_string()));
    }
    if let Err(problem) = std::fs::rename(&tmp, path) {
        let _ = std::fs::remove_file(&tmp);
        return Err(ThemeError(problem.to_string()));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// writing
// ---------------------------------------------------------------------------

/// The `[theme]` block of a theme file: name/created/modified/source.
pub fn meta(name: &str) -> Result<BTreeMap<String, String>, ThemeError> {
    let (sections, _) = read_file(&theme_path(name)?);
    let mut out = BTreeMap::new();
    if let Some(block) = sections.get("theme") {
        for key in META_KEYS {
            if let Some(value) = block.get(key) {
                out.insert(key.to_owned(), value.clone());
            }
        }
    }
    Ok(out)
}

/// The recorded import origin, `fmt:path`, or `""` when hand-made.
pub fn source_of(name: &str) -> Result<String, ThemeError> {
    Ok(meta(name)?.get("source").cloned().unwrap_or_default())
}

/// Write the canonical theme file; returns its path.
///
/// `created` is preserved from the file already on disk, `modified` is
/// now, and `source` is kept unless a new origin is passed. All 22 slots
/// are written, palette-then-named, so a gap-filled load is healed.
pub fn save(name: &str, slots: &SlotMap, source: Option<&str>) -> Result<PathBuf, ThemeError> {
    save_with(name, slots, source, &system_now())
}

/// [`save`] with an injected clock (tests pin `created`/`modified`).
pub fn save_with(
    name: &str,
    slots: &SlotMap,
    source: Option<&str>,
    now: &str,
) -> Result<PathBuf, ThemeError> {
    check(name)?;
    std::fs::create_dir_all(themes_dir()).map_err(|e| ThemeError(e.to_string()))?;
    let path = theme_path(name)?;
    let header = meta(name).unwrap_or_default();
    // Canonical order comes from huebox-core's Slots: fill the buffer,
    // then emit in its order — one source of ordering, not two.
    let mut buffer = crate::core::Slots::default();
    for slot in SLOTS {
        buffer.set(slot, &slot_value(slots, slot));
    }
    let mut lines = vec![
        HEADER.to_owned(),
        "[theme]".to_owned(),
        format!("name = \"{name}\""),
        format!(
            "created = \"{}\"",
            header.get("created").map(String::as_str).unwrap_or(now)
        ),
        format!("modified = \"{now}\""),
    ];
    let origin = source.or_else(|| header.get("source").map(String::as_str));
    if let Some(origin) = origin {
        lines.push(format!(
            "source = \"{origin}\"  # import origin, informational"
        ));
    }
    lines.push(String::new());
    lines.push("[colors]".to_owned());
    for (slot, value) in buffer.iter() {
        lines.push(format!("{slot} = \"{value}\""));
    }
    atomic_write(&path, &(lines.join("\n") + "\n"))?;
    Ok(path)
}

/// An existing theme file colliding with `name`, case-insensitively:
/// macOS/Windows fold case, where `Ember` and `ember` are one file.
fn clash(name: &str) -> Option<String> {
    let wanted = name.to_lowercase();
    let entries = std::fs::read_dir(themes_dir()).ok()?;
    for entry in entries.flatten() {
        let entry = entry.file_name().to_string_lossy().into_owned();
        if entry.ends_with(SUFFIX) && entry[..entry.len() - SUFFIX.len()].to_lowercase() == wanted {
            return Some(entry);
        }
    }
    None
}

/// Create a theme file; refuses to replace one without `force`.
/// The library directory is created lazily — listing on a machine that
/// never made a theme writes nothing.
pub fn create(
    name: &str,
    slots: &SlotMap,
    source: Option<&str>,
    force: bool,
) -> Result<PathBuf, ThemeError> {
    create_with(name, slots, source, force, &system_now())
}

/// [`create`] with an injected clock.
pub fn create_with(
    name: &str,
    slots: &SlotMap,
    source: Option<&str>,
    force: bool,
    now: &str,
) -> Result<PathBuf, ThemeError> {
    check(name)?;
    std::fs::create_dir_all(themes_dir()).map_err(|e| ThemeError(e.to_string()))?;
    if !force && clash(name).is_some() {
        return Err(ThemeError(format!(
            "theme {name} already exists (--force replaces it)"
        )));
    }
    save_with(name, slots, source, now)
}

// ---------------------------------------------------------------------------
// reading
// ---------------------------------------------------------------------------

/// All 22 slots of a theme; gaps arrive as `MISSING` grey.
///
/// Returns `(slots, warnings)`: unknown keys/sections are dropped and
/// counted, non-colour values ignored with a line each, gaps summarised.
/// Gap-tolerant by design: a hand-written theme with three colours still
/// opens, and the next save heals it.
pub fn load(name: &str) -> Result<(SlotMap, Vec<String>), ThemeError> {
    let path = theme_path(name)?;
    if !path.is_file() {
        return Err(ThemeError(format!("no such theme: {name}")));
    }
    let (sections, mut dropped) = read_file(&path);
    let mut warnings = Vec::new();
    let mut slots = SlotMap::new();
    if let Some(colors) = sections.get("colors") {
        for (key, value) in colors {
            if !SLOTS.contains(&key.as_str()) {
                dropped += 1;
            } else if is_hex(value) {
                slots.insert(key.clone(), normalize_hex(value));
            } else {
                warnings.push(format!(
                    "{name}: {key} = '{value}' is not a colour - ignored"
                ));
            }
        }
    }
    for (section, block) in &sections {
        if section == "colors" {
            continue;
        }
        for key in block.keys() {
            if section != "theme" || !META_KEYS.contains(&key.as_str()) {
                dropped += 1;
            }
        }
    }
    if dropped > 0 {
        warnings.push(format!("{name}: dropped {dropped} unknown key(s) on save"));
    }
    let gaps: Vec<&str> = SLOTS
        .iter()
        .filter(|slot| !slots.contains_key::<str>(slot))
        .copied()
        .collect();
    if !gaps.is_empty() {
        warnings.push(format!(
            "{name}: {} slot(s) have no value, shown grey until you save: {}",
            gaps.len(),
            gaps.join(", ")
        ));
    }
    for slot in gaps {
        slots.insert(slot.to_owned(), MISSING.to_owned());
    }
    Ok((slots, warnings))
}

/// `(name, path, mtime)` for every theme file, sorted by name.
/// A file whose stem is not a legal theme name is not listed.
pub fn list_themes() -> Vec<(String, PathBuf, f64)> {
    let mut entries = match std::fs::read_dir(themes_dir()) {
        Ok(entries) => entries,
        Err(_) => return Vec::new(),
    }
    .flatten()
    .map(|entry| entry.file_name().to_string_lossy().into_owned())
    .collect::<Vec<_>>();
    entries.sort();
    let mut out = Vec::new();
    for entry in entries {
        if !entry.ends_with(SUFFIX) {
            continue;
        }
        let name = entry[..entry.len() - SUFFIX.len()].to_owned();
        if !valid_name(&name) {
            continue;
        }
        let path = themes_dir().join(&entry);
        let mtime = std::fs::metadata(&path)
            .and_then(|meta| meta.modified())
            .ok()
            .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64());
        if let Some(mtime) = mtime {
            out.push((name, path, mtime));
        }
    }
    out
}

// ---------------------------------------------------------------------------
// the terminal side
// ---------------------------------------------------------------------------

/// The slots a terminal config holds right now — an import source.
pub fn read_terminal<F: FormatStub>(fmt: &str, path: &Path) -> SlotMap {
    F::read(fmt, path)
}

/// Where [`export_ghostty_native`] puts `name`: Ghostty's own theme dir.
pub fn ghostty_native_path(name: &str) -> Result<PathBuf, ThemeError> {
    check(name)?;
    Ok(ghostty_themes_dir().join(name))
}

/// A theme file with every key Ghostty knows, in Ghostty's dialect.
/// Placeholders are real colours, spliced by the same line-level writer a
/// push uses — a reader sees a finished file or the old one.
fn ghostty_template() -> String {
    let mut lines = vec![GHOSTTY_HEADER.to_owned()];
    for index in 0..16 {
        lines.push(format!("palette = {index}=#000000"));
    }
    for slot in &SLOTS[16..] {
        lines.push(format!("{slot} = #000000"));
    }
    lines.join("\n") + "\n"
}

/// Write the theme as a Ghostty theme file; returns its path.
///
/// All 22 slots in Ghostty's own flat dialect, named after the theme it
/// holds — one theme's colours never land in another theme's file.
/// Written to a sibling tmp, spliced, then renamed: never half-written.
/// A missing slot becomes the `MISSING` grey a push sends.
pub fn export_ghostty_native<F: FormatStub>(
    name: &str,
    slots: &SlotMap,
) -> Result<PathBuf, ThemeError> {
    let path = ghostty_native_path(name)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| ThemeError(e.to_string()))?;
    }
    let tmp = path.to_string_lossy().into_owned() + &format!(".{}.tmp", std::process::id());
    let result = (|| {
        std::fs::write(&tmp, ghostty_template()).map_err(|e| ThemeError(e.to_string()))?;
        let full: SlotMap = SLOTS
            .iter()
            .map(|slot| (slot.to_string(), slot_value(slots, slot)))
            .collect();
        F::write("ghostty", Path::new(&tmp), &full).map_err(|e| ThemeError(e.to_string()))?;
        std::fs::rename(&tmp, &path).map_err(|e| ThemeError(e.to_string()))
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    result?;
    Ok(path)
}

/// The colour slots `main` sets inline — the export's shadows.
///
/// Ghostty loads the theme file first and the main config second, so any
/// colour key the main config carries wins. Empty when there is no main
/// config, no file behind it, or no colour in it.
pub fn ghostty_overrides<F: FormatStub>(main: Option<&Path>) -> SlotMap {
    match main {
        Some(path) => F::read("ghostty", path),
        None => SlotMap::new(),
    }
}

/// Delete the colour lines `main` carries inline; returns how many went.
///
/// Every line the Ghostty rules match goes, everything else stays
/// byte-identical. Line-level like every write to somebody else's file:
/// compare first, write second — nothing to remove keeps the mtime.
/// A missing/unreadable file removes nothing and returns `Ok(0)`; a
/// FAILED delete propagates like Python's raising `open(..., "w")` —
/// success is never reported for bytes that never landed.
pub fn clear_ghostty_overrides<F: FormatStub>(main: Option<&Path>) -> std::io::Result<usize> {
    let path = match main {
        Some(path) => path,
        None => return Ok(0),
    };
    let bytes = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(_) => return Ok(0),
    };
    let lines: Vec<&[u8]> = bytes.split(|b| *b == b'\n').collect();
    let kept: Vec<&[u8]> = lines
        .iter()
        .filter(|line| {
            let text = String::from_utf8_lossy(line);
            !F::color_line("ghostty", &text)
        })
        .copied()
        .collect();
    let removed = lines.len() - kept.len();
    if removed == 0 {
        return Ok(0);
    }
    let mut out = Vec::new();
    for (i, line) in kept.iter().enumerate() {
        if i > 0 {
            out.push(b'\n');
        }
        out.extend_from_slice(line);
    }
    std::fs::write(path, out)?;
    Ok(removed)
}

/// What one `push` did: `lines` is the report, `failed` the exit code,
/// `pushed` the `fmt: path` pairs a status line names, `reloaded` the
/// formats whose terminal re-read its config, `overrides` the Ghostty
/// inline colours shadowing an export — `(main, slots)` or `None` — which
/// arms the editor's delete-or-keep popup.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PushResult {
    /// Report lines for stderr.
    pub lines: Vec<String>,
    /// Targets written, in order.
    pub pushed: Vec<(String, PathBuf)>,
    /// Whether the caller exits 1 (truth stays as written regardless).
    pub failed: bool,
    /// Formats whose terminal was asked to re-read its config.
    pub reloaded: Vec<String>,
    /// Ghostty inline shadows: `(main config, slots)`.
    pub overrides: Option<(PathBuf, Vec<String>)>,
}

impl PushResult {
    /// The `no_push` outcome: nothing touched, nothing reported.
    pub fn empty() -> Self {
        PushResult {
            lines: Vec::new(),
            pushed: Vec::new(),
            failed: false,
            reloaded: Vec::new(),
            overrides: None,
        }
    }

    /// Whether the caller owes the reload advice line: something was
    /// pushed, nothing was reloaded, and at least one pushed target does
    /// not live-reload on its own (alacritty never wants the line).
    pub fn needs_reload_advice(&self) -> bool {
        !self.pushed.is_empty()
            && self.reloaded.is_empty()
            && self.pushed.iter().any(|(fmt, _)| !live_reloads(fmt))
    }
}

/// Formats that re-read their config without being asked: never
/// signalled, never owed the advice line. Alacritty's
/// `live_config_reload` is on by default, so a written config is already
/// live.
fn live_reloads(fmt: &str) -> bool {
    fmt == "alacritty"
}

/// How a terminal is told to re-read its config. Only terminals with a
/// real interface: kitty answers its own remote control, ghostty is asked
/// with a signal. Alacritty is absent on purpose: it live-reloads, so it
/// is never signalled and never owed the advice line.
pub static RELOAD_COMMANDS: &[(&str, &[&str])] = &[("kitty", &["kitty", "@", "load-config"])];

/// Where the reload looks for a running terminal (a parameter, so tests
/// hand it a directory of fake processes instead of the real machine).
pub const PROC_DEFAULT: &str = "/proc";

/// Signal numbers for the reload path. The live backend shells to
/// `kill(1)` with the SYMBOLIC flags (`-USR1`/`-USR2`, matched below),
/// so these integers only surface for exotic signals via `-{other}`;
/// the values are the Linux numbering (macOS shares the USR1/USR2
/// names; Windows has no `kill(1)` and every send fails best-effort).
pub const SIGUSR1: i32 = 10;
/// Signal numbers (see [`SIGUSR1`]).
pub const SIGUSR2: i32 = 12;

/// The seams [`reload_terminal_with`] reaches the machine through, so
/// tests script them instead of signalling their own terminal.
pub trait ReloadBackend {
    /// Send `sig` to `pid`; `false` on any failure.
    fn send_signal(&self, pid: u32, sig: i32) -> bool;
    /// Run `argv` with no tty and a 5s timeout; `true` on exit 0.
    fn run_command(&self, argv: &[&str]) -> bool;
}

/// The live backend: `kill(1)` for signals, spawned processes for the
/// kitty fallback, stdio always null.
pub struct SystemReloader;

impl ReloadBackend for SystemReloader {
    fn send_signal(&self, pid: u32, sig: i32) -> bool {
        let flag = match sig {
            SIGUSR1 => "-USR1".to_owned(),
            SIGUSR2 => "-USR2".to_owned(),
            other => format!("-{other}"),
        };
        Command::new("kill")
            .arg(flag)
            .arg(pid.to_string())
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }

    fn run_command(&self, argv: &[&str]) -> bool {
        let (head, args) = match argv.split_first() {
            Some(pair) => pair,
            None => return false,
        };
        let mut child = match Command::new(head)
            .args(args)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
        {
            Ok(child) => child,
            Err(_) => return false,
        };
        // std has no `wait_timeout`: wait on a thread, give up after 5s
        // (the child is reaped by the thread; the save does not hang).
        let (done, result) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = done.send(child.wait());
        });
        match result.recv_timeout(Duration::from_secs(5)) {
            Ok(Ok(status)) => status.success(),
            _ => false,
        }
    }
}

/// The owning Ghostty application, not a surface: the process named
/// `ghostty` whose command line carries `--gtk-single-instance`.
fn is_ghostty_app(comm: &str, cmdline: &str) -> bool {
    comm.trim() == "ghostty" && cmdline.contains("ghostty") && cmdline.contains("single-instance")
}

/// The application pid from `(pid, comm, cmdline)` rows, lowest first —
/// one chooser for the `/proc` walk and the macOS `pgrep`/`ps` fallback,
/// so per-window surfaces (no `--gtk-single-instance`) never win on
/// either platform.
fn pick_ghostty_app(mut rows: Vec<(u32, String, String)>) -> Option<u32> {
    rows.sort();
    rows.into_iter()
        .find(|(_, comm, cmdline)| is_ghostty_app(comm, cmdline))
        .map(|(pid, _, _)| pid)
}

/// `(pid, comm, cmdline)` rows from a `/proc`-shaped tree.
#[cfg(not(target_os = "macos"))]
fn ghostty_app_rows_proc(proc_root: &Path) -> Vec<(u32, String, String)> {
    let mut entries: Vec<String> = std::fs::read_dir(proc_root)
        .ok()
        .map(|entries| {
            entries
                .flatten()
                .map(|entry| entry.file_name().to_string_lossy().into_owned())
                .filter(|name| !name.is_empty() && name.bytes().all(|b| b.is_ascii_digit()))
                .collect()
        })
        .unwrap_or_default();
    entries.sort_by(|a, b| (a.len(), a).cmp(&(b.len(), b)));
    let mut rows = Vec::new();
    for entry in entries {
        let pid: u32 = match entry.parse() {
            Ok(pid) => pid,
            Err(_) => continue,
        };
        let comm = match std::fs::read(proc_root.join(&entry).join("comm")) {
            Ok(comm) => String::from_utf8_lossy(&comm).into_owned(),
            Err(_) => continue,
        };
        let cmdline = match std::fs::read(proc_root.join(&entry).join("cmdline")) {
            Ok(cmdline) => String::from_utf8_lossy(&cmdline).into_owned(),
            Err(_) => continue,
        };
        rows.push((pid, comm, cmdline));
    }
    rows
}

/// One command's stdout, or `None`; stdio never touches the tty.
#[cfg(target_os = "macos")]
fn capture_stdout(argv: &[&str]) -> Option<String> {
    let (head, args) = argv.split_first()?;
    let output = Command::new(head)
        .args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout).ok()
}

/// `(pid, comm, cmdline)` rows without `/proc`: candidates from
/// `pgrep -x ghostty`, each confirmed through `ps`. `pgrep -x` already
/// matches the name exactly; the `ps` args carry the `--gtk-single-instance`
/// check, so per-window surfaces are listed but never picked.
#[cfg(any(target_os = "macos", test))]
fn ghostty_app_rows_macos(run: &dyn Fn(&[&str]) -> Option<String>) -> Vec<(u32, String, String)> {
    let listing = match run(&["pgrep", "-x", "ghostty"]) {
        Some(listing) => listing,
        None => return Vec::new(),
    };
    let mut rows = Vec::new();
    for line in listing.split_whitespace() {
        let pid: u32 = match line.parse() {
            Ok(pid) => pid,
            Err(_) => continue,
        };
        let args = match run(&["ps", "-p", line, "-o", "args="]) {
            Some(args) => args,
            None => continue,
        };
        rows.push((pid, "ghostty".to_owned(), args));
    }
    rows
}

/// The running Ghostty application process, or `None`.
///
/// Ghostty 1.3 has no CLI reload action, but its application handles
/// `SIGUSR2` as "reload the configuration". Found by process name
/// `ghostty` plus `--gtk-single-instance` on the command line (the owning
/// application, not a surface): a `/proc` walk on Linux, a
/// `pgrep`/`ps` fallback on macOS, which has no `/proc`.
pub fn ghostty_app_pid(proc_root: &Path) -> Option<u32> {
    // macOS has no /proc: pgrep/ps instead. `proc_root` is the Linux seam
    // (tests hand it a directory of fake processes); macOS ignores it.
    #[cfg(target_os = "macos")]
    {
        let _ = proc_root;
        return pick_ghostty_app(ghostty_app_rows_macos(&capture_stdout));
    }
    #[cfg(not(target_os = "macos"))]
    pick_ghostty_app(ghostty_app_rows_proc(proc_root))
}

/// Whether this process runs inside a kitty window.
///
/// `kitty @` without `KITTY_LISTEN_ON` talks to the controlling terminal
/// through escape codes — from any other terminal that is a doomed wait
/// for a reply that never comes. So the fallback only runs where a kitty
/// can actually answer: explicit env, or a kitty ancestor.
pub fn in_kitty(proc_root: &Path, pid: u32) -> bool {
    for var in ["KITTY_PID", "KITTY_LISTEN_ON"] {
        if !std::env::var(var).unwrap_or_default().is_empty() {
            return true;
        }
    }
    let mut pid = pid;
    for _ in 0..64 {
        let stat = match std::fs::read_to_string(proc_root.join(pid.to_string()).join("stat")) {
            Ok(stat) => stat,
            Err(_) => return false,
        };
        let after = match stat.rfind(')') {
            Some(at) => &stat[at + 1..],
            None => return false,
        };
        let ppid: u32 = match after
            .split_whitespace()
            .nth(1)
            .and_then(|field| field.parse().ok())
        {
            Some(ppid) => ppid,
            None => return false,
        };
        if ppid <= 1 {
            return false;
        }
        let comm = match std::fs::read(proc_root.join(ppid.to_string()).join("comm")) {
            Ok(comm) => comm,
            Err(_) => return false,
        };
        if String::from_utf8_lossy(&comm).trim() == "kitty" {
            return true;
        }
        pid = ppid;
    }
    false
}

/// A PID from the environment, or `None` when missing or junk.
pub fn env_pid(name: &str) -> Option<u32> {
    match std::env::var(name).unwrap_or_default().parse::<i64>() {
        Ok(pid) if pid > 0 => u32::try_from(pid).ok(),
        _ => None,
    }
}

/// Ask `fmt`'s terminal to re-read its config; the report line, or `""`.
///
/// Best effort by contract: a terminal that is not there, a signal that
/// lands nowhere and a command that is not installed are all the same
/// event — the colours are on disk either way. Signals beat subprocesses
/// wherever a terminal has one: `kitty @` without a socket talks to the
/// controlling tty through escape codes, and under the editor that channel
/// is the app's own input. Alacritty always returns `""`: it live-reloads,
/// so it is never signalled.
pub fn reload_terminal(fmt: &str) -> String {
    reload_terminal_with(fmt, &SystemReloader, Path::new(PROC_DEFAULT))
}

/// [`reload_terminal`] with injected machine seams (tests script these).
pub fn reload_terminal_with<R: ReloadBackend>(fmt: &str, backend: &R, proc_root: &Path) -> String {
    if fmt == "ghostty" {
        let pid = match ghostty_app_pid(proc_root) {
            Some(pid) => pid,
            None => return String::new(),
        };
        if !backend.send_signal(pid, SIGUSR2) {
            return String::new();
        }
        return format!("ghostty: reloaded (config re-read, pid {pid})");
    }
    if fmt == "kitty" {
        if let Some(pid) = env_pid("KITTY_PID") {
            if !backend.send_signal(pid, SIGUSR1) {
                return String::new();
            }
            return format!("kitty: reloaded (config re-read, pid {pid})");
        }
        if !in_kitty(proc_root, std::process::id()) {
            return String::new();
        }
    }
    let command = match RELOAD_COMMANDS.iter().find(|(name, _)| *name == fmt) {
        Some((_, command)) => command,
        None => return String::new(),
    };
    if !backend.run_command(command) {
        return String::new();
    }
    format!("{fmt}: reloaded ({})", command.join(" "))
}

/// The formats a push aims at, in order.
///
/// `to` (comma entries or several) wins; otherwise the forced format,
/// otherwise `None` — "whatever terminal this is", answered by `resolve`.
fn target_names<F: FormatStub>(
    to: &[String],
    fmt: Option<&str>,
) -> Result<Vec<Option<String>>, ThemeError> {
    if !to.is_empty() {
        let mut names = Vec::new();
        for part in to {
            for piece in part.split(',') {
                let name = piece.trim().to_owned();
                if name.is_empty() {
                    continue;
                }
                if !F::format_names().contains(&name.as_str()) {
                    return Err(ThemeError(unknown_format(&name, F::format_names())));
                }
                if !names.contains(&Some(name.clone())) {
                    names.push(Some(name));
                }
            }
        }
        if names.is_empty() {
            return Err(ThemeError("--to needs at least one format".to_owned()));
        }
        return Ok(names);
    }
    Ok(vec![fmt.map(str::to_owned)])
}

/// The file `theme = name` means: Ghostty's own themes dir, same value.
fn theme_file_for(name: &str) -> PathBuf {
    ghostty_themes_dir().join(name)
}

fn realpath(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// Should this Ghostty target be exported as a theme file?
///
/// One sentence: **a theme's colours never land in a file that belongs to
/// another theme.** When the config is organised by theme — its `theme =`
/// line names the file the colours live in — the save is exported under
/// the theme's own name and the pointer repointed. When the colours are
/// inline (or in an include), they are edited where they are. `force` is
/// `--ghostty-native`, `in_place` is `--ghostty-in-place`.
fn export_or_edit(
    found: &str,
    target: &Path,
    name: Option<&str>,
    main: Option<&Path>,
    force: bool,
    in_place: bool,
) -> bool {
    if found != "ghostty" || name.is_none() {
        return false;
    }
    if in_place {
        return false;
    }
    if force || main.is_none() {
        return true;
    }
    let main = main.expect("checked");
    let named = match ghostty_theme_name(main) {
        Some(named) => named,
        None => return false,
    };
    let here = realpath(target);
    here == realpath(&theme_file_for(&named)) || here == realpath(main)
}

/// Complain when `target` is *another* theme's file, else `None`.
/// The last guard under the in-place path: refused whenever the file is
/// named after a different theme and the push would actually change it.
fn foreign_theme(
    target: &Path,
    name: Option<&str>,
    existing: &SlotMap,
    slots: &SlotMap,
) -> Option<String> {
    let name = name?;
    if target
        .file_name()
        .is_some_and(|base| base.to_string_lossy() == name)
    {
        return None;
    }
    let directory = realpath(target);
    let directory = directory.parent()?;
    if directory != realpath(&ghostty_themes_dir()) {
        return None; // inline config or an include
    }
    let changes = SLOTS
        .iter()
        .filter(|slot| existing.contains_key::<str>(slot))
        .any(|slot| slot_value(slots, slot) != existing[*slot]);
    if !changes {
        return None; // would write nothing anyway
    }
    Some(format!(
        "refusing to write theme '{name}' into {}: that is the theme file for '{}', \
         and a save must never cross two themes' names - drop --ghostty-in-place to \
         export '{name}' as its own theme file",
        target.display(),
        target
            .file_name()
            .map(|base| base.to_string_lossy())
            .unwrap_or_default()
    ))
}

/// The theme a config points at that is not on disk, else `None`.
/// A dangling pointer is a target rather than a refusal: the export is
/// what repairs it, and the report says the file was missing.
pub(crate) fn dangling_pointer(main: &Path) -> Option<String> {
    let named = ghostty_theme_name(main)?;
    if theme_file_for(&named).is_file() {
        return None;
    }
    Some(named)
}

/// Export a Ghostty theme file, then point the main config at it.
/// Returns `(path, lines, failed, overrides)`. Neither write is rolled
/// back if the other misses — a theme file without a pointer is harmless,
/// and the report says so.
type NativeOutcome = (
    Option<PathBuf>,
    Vec<String>,
    bool,
    Option<(PathBuf, Vec<String>)>,
);
fn push_native<F: FormatStub>(
    name: &str,
    slots: &SlotMap,
    main: &Path,
    dangling: Option<String>,
) -> NativeOutcome {
    let path = match export_ghostty_native::<F>(name, slots) {
        Ok(path) => path,
        Err(problem) => {
            return (
                None,
                vec![format!("ghostty: {}: {problem}", main.display())],
                true,
                None,
            );
        }
    };
    let over = ghostty_overrides::<F>(Some(main));
    let action = match ensure_theme_pointer(main, name) {
        Ok(action) => action,
        Err(problem) => {
            return (
                None,
                vec![format!("ghostty: {}: {problem}", main.display())],
                true,
                None,
            );
        }
    };
    let mut lines = vec![
        format!("ghostty: exported {}", path.display()),
        format!(
            "ghostty: theme = {name} {} in {}",
            action.as_str(),
            main.display()
        ),
    ];
    if let Some(missing) = dangling {
        lines.push(format!(
            "ghostty: {} pointed at '{missing}', which was not on disk - the colours are \
             back in a file of that name now",
            main.display()
        ));
    }
    let mut overrides = None;
    if !over.is_empty() {
        lines.push(format!(
            "ghostty: the colours in {} are now shadowed by {} - remove the inline ones to \
             change them again",
            main.display(),
            path.display()
        ));
        let mut keys: Vec<String> = over.keys().cloned().collect();
        keys.sort();
        overrides = Some((main.to_path_buf(), keys));
    }
    (Some(path), lines, false, overrides)
}

/// Push options: one struct for the seams `push` does not own.
/// `to` entries may each hold a comma list (`"ghostty,kitty"`) or a single
/// name; `path` is an explicit config (the `--config` seam) for exactly
/// one target.
#[derive(Clone, Debug, Default)]
pub struct PushOptions {
    /// Target formats (empty: forced `fmt`, else every terminal).
    pub to: Vec<String>,
    /// Forced format.
    pub fmt: Option<String>,
    /// Explicit config path.
    pub path: Option<PathBuf>,
    /// Push nothing at all (truth-only session).
    pub no_push: bool,
    /// `--ghostty-native`: export even where colours are inline.
    pub ghostty_native: bool,
    /// Theme name: what an export is *called* (absent in direct sessions).
    pub name: Option<String>,
    /// `--ghostty-in-place`: never export.
    pub ghostty_in_place: bool,
    /// Ask pushed terminals to re-read their configs.
    pub reload: bool,
}

/// Write a saved buffer into the terminal config(s) holding colours.
///
/// Two paths for Ghostty, decided by the config: the **export** (theme
/// file + repointed pointer) where the config is organised by theme, the
/// **in-place** line-level splice where colours are inline or included.
/// Either way a theme's colours never land in another theme's file.
///
/// Three rules the terminal side does not negotiate: a config with no
/// colours is never a target; keys the target does not carry are reported,
/// never inserted; every target is tried even when an earlier one failed.
///
/// Truth-then-push lives here as a shape, not a sequence: this function
/// takes *slots only* — it never reads or writes a theme file, so the
/// truth file written before this call is never rolled back, and a failed
/// push is a report plus `failed`. The one sanctioned bypass is the same
/// shape: a preview push (Ctrl+A) hands a dirty buffer straight here,
/// truth untouched by construction.
pub fn push<F: FormatStub>(slots: &SlotMap, opts: &PushOptions) -> Result<PushResult, ThemeError> {
    push_with::<F, SystemReloader>(slots, opts, &SystemReloader, Path::new(PROC_DEFAULT))
}

/// [`push`] with injected machine seams (tests script the reload).
pub fn push_with<F: FormatStub, R: ReloadBackend>(
    slots: &SlotMap,
    opts: &PushOptions,
    backend: &R,
    proc_root: &Path,
) -> Result<PushResult, ThemeError> {
    if opts.no_push {
        return Ok(PushResult::empty());
    }
    let mut names = target_names::<F>(&opts.to, opts.fmt.as_deref())?;
    let all_targets = opts.to.is_empty() && opts.fmt.is_none() && opts.path.is_none();
    if all_targets {
        // Every format holding colours, not the first one detected — the
        // loop skips the absent ones silently, so this over-asks on purpose.
        names = F::format_names()
            .iter()
            .map(|name| Some(name.to_string()))
            .collect();
    }
    if opts.path.is_some() && names.len() != 1 {
        return Err(ThemeError(format!(
            "an explicit config path pushes exactly one target, got {} target names",
            names.len()
        )));
    }
    let explicit = if names.len() == 1 {
        opts.path.clone()
    } else {
        None
    };
    let mut lines: Vec<String> = Vec::new();
    let mut pushed: Vec<(String, PathBuf)> = Vec::new();
    let mut failed = false;
    let mut overrides = None;

    for wanted in &names {
        // Push-mode resolution (decision 54): /etc candidates are skipped and
        // an explicit --config under /etc is refused by resolve_for_push itself.
        let (found, target, error) = resolve_for_push::<F>(wanted.as_deref(), explicit.as_deref());
        let label = wanted.clone().unwrap_or_else(|| "terminal".to_owned());
        // The config holding the `theme =` line, asked before the colours:
        // a dangling pointer is a target even though nothing resolves.
        let mut main: Option<PathBuf> = None;
        if wanted.as_deref().unwrap_or("ghostty") == "ghostty" {
            main = ghostty_main_config::<F>(explicit.as_deref());
        }
        let dangling = main.as_deref().and_then(dangling_pointer);
        let (found, target) =
            match (found, target, error) {
                (Some(found), Some(target), _) => (found, target),
                (_, _, error) => {
                    if dangling.is_some() && opts.name.is_some() && !opts.ghostty_in_place {
                        (
                            "ghostty".to_owned(),
                            main.clone().expect("dangling needs main"),
                        )
                    } else if all_targets && dangling.is_none() {
                        continue; // no such terminal here - not an error
                    } else if let Some(missing) = dangling {
                        let advice = if opts.name.is_none() {
                            "save the buffer as a theme (N) and huebox will export it"
                        } else {
                            "drop --ghostty-in-place to have huebox export it"
                        };
                        lines.push(format!(
                        "{label}: {} points at theme '{missing}', which is not on disk - {advice}",
                        main.as_deref().map(|main| main.display().to_string()).unwrap_or_default()
                    ));
                        failed = true;
                        continue;
                    } else {
                        let reason = error.unwrap_or_else(|| {
                            format!(
                                "cannot tell which format {} is - pass --format with one of {}",
                                explicit
                                    .as_deref()
                                    .map(|path| path.display().to_string())
                                    .unwrap_or_else(|| "?".to_owned()),
                                F::format_names().join(", ")
                            )
                        });
                        lines.push(format!("{label}: {reason}"));
                        failed = true;
                        continue;
                    }
                }
            };
        if found == "ghostty" && main.is_none() {
            main = ghostty_main_config::<F>(explicit.as_deref());
        }
        if export_or_edit(
            &found,
            &target,
            opts.name.as_deref(),
            main.as_deref(),
            opts.ghostty_native,
            opts.ghostty_in_place,
        ) {
            let main = match main {
                Some(main) => main,
                None => {
                    lines.push(
                        "ghostty: no ghostty config to point at (no config.ghostty or config found)"
                            .to_owned(),
                    );
                    failed = true;
                    continue;
                }
            };
            if dangling.is_none() && !config_holds_colours::<F>("ghostty", &main) {
                lines.push(format!(
                    "ghostty: no colours in {} - not a push target",
                    main.display()
                ));
                failed = true;
                continue;
            }
            let name = opts.name.as_deref().expect("export needs a name");
            let (path, extra, bad, over) = push_native::<F>(name, slots, &main, dangling);
            lines.extend(extra);
            if bad {
                failed = true;
                continue;
            }
            pushed.push((found, path.expect("exported path")));
            if over.is_some() {
                overrides = over;
            }
            continue;
        }
        let existing = F::read(&found, &target);
        if existing.is_empty() {
            if all_targets {
                continue; // resolved colourless - not a target either
            }
            lines.push(format!(
                "{found}: no colours in {} - not a push target",
                target.display()
            ));
            failed = true;
            continue;
        }
        if let Some(refusal) = foreign_theme(&target, opts.name.as_deref(), &existing, slots) {
            lines.push(format!("ghostty: {refusal}"));
            failed = true;
            continue;
        }
        if let Err(problem) = F::write(&found, &target, slots) {
            lines.push(format!("{found}: {}: {problem}", target.display()));
            failed = true;
            continue;
        }
        pushed.push((found.clone(), target.clone()));
        lines.push(format!("{found}: pushed to {}", target.display()));
        let missing: Vec<&str> = SLOTS
            .iter()
            .filter(|slot| !existing.contains_key::<str>(slot))
            .copied()
            .collect();
        if !missing.is_empty() {
            lines.push(format!(
                "{found}: not carried by this config: {}",
                missing.join(", ")
            ));
        }
    }

    let mut reloaded = Vec::new();
    if all_targets && pushed.is_empty() && !failed {
        lines.push(format!(
            "found no terminal config with colours; pass --format with one of {}",
            F::format_names().join(", ")
        ));
        failed = true;
    }
    if opts.reload {
        for (hit, _) in &pushed {
            let note = reload_terminal_with(hit, backend, proc_root);
            if !note.is_empty() {
                lines.push(note);
                reloaded.push(hit.clone());
            }
        }
    }

    Ok(PushResult {
        lines,
        pushed,
        failed,
        reloaded,
        overrides,
    })
}

/// The built-in fallback ramp for `new` when no terminal has colours to
/// seed from: a neutral dark base with a readable foreground, an inverting
/// cursor, a selection one step lighter, and the usual 0-7 / 8-15 split.
pub static RAMP: [(&str, &str); 22] = [
    ("background", "#101014"),
    ("foreground", "#e6e6ea"),
    ("cursor-color", "#e6e6ea"),
    ("cursor-text", "#101014"),
    ("selection-background", "#2a2a34"),
    ("selection-foreground", "#e6e6ea"),
    ("palette-0", "#101014"),
    ("palette-1", "#a83232"),
    ("palette-2", "#3f7a3f"),
    ("palette-3", "#a88a3f"),
    ("palette-4", "#3f6a8a"),
    ("palette-5", "#8a3f6a"),
    ("palette-6", "#3f8a8a"),
    ("palette-7", "#b0b0b8"),
    ("palette-8", "#d0d0d8"),
    ("palette-9", "#e06c6c"),
    ("palette-10", "#6cc06c"),
    ("palette-11", "#e0c06c"),
    ("palette-12", "#6c9ce0"),
    ("palette-13", "#e06c9c"),
    ("palette-14", "#6cc0c0"),
    ("palette-15", "#f0f0f8"),
];

/// `RAMP` as a slot map.
pub fn ramp_slots() -> SlotMap {
    RAMP.iter()
        .map(|(slot, value)| (slot.to_string(), value.to_string()))
        .collect()
}

// ---------------------------------------------------------------------------
// state.toml
// ---------------------------------------------------------------------------

/// The theme `state.toml` points at, or `None`.
///
/// A missing, unreadable, corrupt or nonsense state file all read as
/// `None`: the caller falls back to direct mode, and the next
/// `set_current` rewrites the file whole.
pub fn current() -> Option<String> {
    let (sections, _) = read_file(&state_path());
    let name = sections.get("")?.get("current")?.clone();
    if valid_name(&name) {
        Some(name)
    } else {
        None
    }
}

/// Point the state at `name`; rewrites the file even if it was corrupt.
pub fn set_current(name: &str) -> Result<(), ThemeError> {
    check(name)?;
    std::fs::create_dir_all(home()).map_err(|e| ThemeError(e.to_string()))?;
    atomic_write(&state_path(), &format!("current = \"{name}\"\n"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::library::format::FormatStub;
    use crate::library::providers::tests::Fake as Mem;
    use crate::library::testutil::{lock_env, temp_dir, TestDir};
    use crate::library::ThemeError;

    /// 22 distinct values, so a slot mix-up cannot hide.
    fn full_slots() -> SlotMap {
        SLOTS
            .iter()
            .enumerate()
            .map(|(i, slot)| (slot.to_string(), format!("#{i:02x}{i:02x}{i:02x}")))
            .collect()
    }

    const NAMED_TAIL: [&str; 6] = [
        "background",
        "foreground",
        "cursor-color",
        "cursor-text",
        "selection-background",
        "selection-foreground",
    ];

    fn ghostty_text(slots: &SlotMap) -> String {
        let mut lines = vec![
            "# fixture config".to_owned(),
            "font-size = 12".to_owned(),
            String::new(),
        ];
        for i in 0..16 {
            lines.push(format!("palette = {i}={}", slots[&format!("palette-{i}")]));
        }
        for name in NAMED_TAIL {
            lines.push(format!("{name} = {}", slots[name]));
        }
        lines.join("\n") + "\n"
    }

    fn kitty_text() -> String {
        "# kitty fixture\n\
         font_family      SauceCodePro Nerd Font\n\n\
         background            #0f0f1a\n\
         foreground            #ededfe\n\
         cursor                #ededfe\n\
         selection_background  #202036\n\
         selection_foreground  #ededff\n\
         color0  #0a0a13\n\
         color1  #ff0067\n\
         color15 #f8f8ff\n"
            .to_owned()
    }

    /// Isolated `$XDG_CONFIG_HOME` (+`HOME`) plus a fixture config on disk.
    struct Home {
        _guard: crate::library::testutil::EnvGuard,
        dir: TestDir,
        config: PathBuf,
    }

    impl Home {
        fn setup() -> Self {
            let dir = temp_dir("themes-home");
            let root = dir.root();
            let guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str()), ("HOME", root.as_str())]);
            let config = dir.write("config.ghostty", &ghostty_text(&full_slots()));
            Home {
                _guard: guard,
                dir,
                config,
            }
        }

        fn theme_file(&self, name: &str) -> PathBuf {
            self.dir
                .path()
                .join("huebox")
                .join("themes")
                .join(format!("{name}.toml"))
        }

        fn kitty(&self) -> PathBuf {
            self.dir.write("kitty.conf", &kitty_text())
        }
    }

    fn set_mtime(path: &Path, stamp: std::time::SystemTime) {
        std::fs::File::options()
            .write(true)
            .open(path)
            .unwrap()
            .set_modified(stamp)
            .unwrap();
    }

    fn backdated(path: &Path) -> std::time::SystemTime {
        let stamp = std::time::SystemTime::now() - std::time::Duration::from_secs(60);
        set_mtime(path, stamp);
        stamp
    }

    // -- names -----------------------------------------------------------

    #[test]
    fn name_rules() {
        for name in [
            "ember",
            "a",
            "0",
            "A",
            "my-theme_2",
            &"x".repeat(64),
            "0123456789",
        ] {
            assert!(valid_name(name), "{name}");
        }
        for name in [
            "",
            "-lead",
            "_lead",
            ".hidden",
            "a b",
            "a.b",
            "a/b",
            "a.toml",
            "a\\b",
            "émber",
            &"x".repeat(65),
            "a\nb",
            "a;b",
            "a:b",
            "../escape",
        ] {
            assert!(!valid_name(name), "{name:?}");
        }
        let _guard = lock_env(&[("XDG_CONFIG_HOME", "/tmp/hb-test-names")]);
        assert_eq!(
            theme_path("ember").unwrap(),
            PathBuf::from("/tmp/hb-test-names/huebox/themes/ember.toml")
        );
    }

    // -- home ------------------------------------------------------------

    #[test]
    fn home_layout_and_laziness() {
        let home = Home::setup();
        let root = home.dir.root();
        assert_eq!(home.dir.path().join("huebox"), super::home());
        assert_eq!(themes_dir(), super::home().join("themes"));
        assert_eq!(state_path(), super::home().join("state.toml"));
        let _ = root;
        drop(home);
        // Fallback to ~/.config when XDG is empty.
        let dir = temp_dir("themes-fallback");
        let root = dir.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", ""), ("HOME", root.as_str())]);
        assert_eq!(super::home(), dir.path().join(".config").join("huebox"));
        drop(_guard);
        // Reading never creates anything.
        let dir = temp_dir("themes-lazy");
        let root = dir.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str()), ("HOME", root.as_str())]);
        assert_eq!(current(), None);
        assert!(list_themes().is_empty());
        assert!(matches!(load("ember"), Err(ThemeError(_))));
        assert!(!dir.path().join("huebox").exists());
        // …while creating makes the directories.
        create("ember", &full_slots(), None, false).unwrap();
        assert!(dir
            .path()
            .join("huebox")
            .join("themes")
            .join("ember.toml")
            .is_file());
    }

    // -- write/read ------------------------------------------------------

    #[test]
    fn round_trip_keeps_all_22() {
        let home = Home::setup();
        create("ember", &full_slots(), None, false).unwrap();
        let (slots, warnings) = load("ember").unwrap();
        assert_eq!(slots, full_slots());
        assert!(warnings.is_empty());
        let _ = home;
    }

    #[test]
    fn canonical_layout() {
        let home = Home::setup();
        create_with(
            "ember",
            &full_slots(),
            Some("ghostty:/tmp/config"),
            false,
            "2026-10-01T12:00:00",
        )
        .unwrap();
        let lines: Vec<String> = std::fs::read_to_string(home.theme_file("ember"))
            .unwrap()
            .lines()
            .map(str::to_owned)
            .collect();
        let full = full_slots();
        assert_eq!(lines[0], HEADER);
        assert_eq!(lines[1], "[theme]");
        assert_eq!(lines[2], "name = \"ember\"");
        assert!(lines[3].starts_with("created = \""), "{}", lines[3]);
        assert!(lines[4].starts_with("modified = \""), "{}", lines[4]);
        assert_eq!(
            lines[5],
            "source = \"ghostty:/tmp/config\"  # import origin, informational"
        );
        assert_eq!(lines[6], "");
        assert_eq!(lines[7], "[colors]");
        assert_eq!(
            lines[8..],
            SLOTS
                .iter()
                .map(|slot| format!("{slot} = \"{}\"", full[*slot]))
                .collect::<Vec<_>>()
        );
        assert_eq!(lines.len(), 8 + SLOTS.len());
    }

    #[test]
    fn handmade_gap_and_messy_files() {
        let home = Home::setup();
        // No source recorded: re-save keeps it that way.
        home.dir.write(
            "huebox/themes/bare.toml",
            "[colors]\nbackground = \"#010203\"\n",
        );
        let (slots, _) = load("bare").unwrap();
        assert_eq!(slots["background"], "#010203");
        save("bare", &slots, None).unwrap();
        assert!(!std::fs::read_to_string(home.theme_file("bare"))
            .unwrap()
            .contains("source ="));
        // Gaps load as MISSING grey with one warning…
        home.dir.write(
            "huebox/themes/hollow.toml",
            "[colors]\nbackground = \"#010203\"\n",
        );
        let (slots, warnings) = load("hollow").unwrap();
        assert_eq!(slots.len(), 22);
        assert_eq!(slots["palette-0"], MISSING);
        assert_eq!(warnings.len(), 1);
        assert!(warnings[0].contains("21 slot(s)"), "{:?}", warnings);
        // …and saving heals them.
        save("hollow", &slots, None).unwrap();
        let (healed, _) = load("hollow").unwrap();
        assert_eq!(healed, slots);
        let text = std::fs::read_to_string(home.theme_file("hollow")).unwrap();
        assert!(text.contains("palette-15 = \"#808080\""));
        // Unknown keys and sections are counted once.
        home.dir.write(
            "huebox/themes/messy.toml",
            "[theme]\nname = \"messy\"\nauthor = \"me\"\n[colors]\nbackground = \"#010203\"\n\
             sparkle = \"#040506\"\n[extra]\nwhatever = 1\n",
        );
        let (slots, warnings) = load("messy").unwrap();
        assert_eq!(slots["background"], "#010203");
        assert!(!slots.contains_key("sparkle"));
        assert!(
            warnings.iter().any(|line| line.contains("3 unknown key")),
            "{warnings:?}"
        );
        // Non-colour values are ignored with a line each, plus the summary.
        home.dir.write(
            "huebox/themes/odd.toml",
            "[colors]\nbackground = \"chartreuse\"\nforeground = 0x101014\n",
        );
        let (slots, warnings) = load("odd").unwrap();
        assert_eq!(slots["background"], MISSING);
        assert_eq!(warnings.len(), 3);
    }

    #[test]
    fn loose_syntax_accepted() {
        let home = Home::setup();
        home.dir.write(
            "huebox/themes/loose.toml",
            "# a comment\n\n[colors]\n   background   =   #0A0B0C   # trailing\n\
             foreground = \"#D0D1D2\"\n\"cursor-color\" = '#e0e1e2'\n",
        );
        let (slots, _) = load("loose").unwrap();
        assert_eq!(slots["background"], "#0a0b0c");
        assert_eq!(slots["foreground"], "#d0d1d2");
        assert_eq!(slots["cursor-color"], "#e0e1e2");
        // A hash inside a quoted value is not a comment.
        let (sections, dropped) = parse("[colors]\nbg = \"#123456\"  # ok\n");
        assert_eq!(dropped, 0);
        assert_eq!(sections["colors"]["bg"], "#123456");
        let _ = home;
    }

    #[test]
    fn created_preserved_source_kept_write_atomic() {
        let home = Home::setup();
        create_with(
            "ember",
            &full_slots(),
            Some("kitty:/tmp/kitty.conf"),
            false,
            "2026-10-01T12:00:00",
        )
        .unwrap();
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        save_with("ember", &edited, None, "2026-10-01T12:34:56").unwrap();
        let head = meta("ember").unwrap();
        assert_eq!(head["created"], "2026-10-01T12:00:00");
        assert_eq!(head["modified"], "2026-10-01T12:34:56");
        assert_eq!(source_of("ember").unwrap(), "kitty:/tmp/kitty.conf");
        let siblings: Vec<String> = std::fs::read_dir(home.theme_file("ember").parent().unwrap())
            .unwrap()
            .flatten()
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(siblings, vec!["ember.toml"]);
    }

    #[test]
    fn missing_and_illegal_names() {
        let home = Home::setup();
        assert_eq!(load("nope").unwrap_err().0, "no such theme: nope");
        assert_eq!(load("no/slashes").unwrap_err().0, "invalid theme name");
        assert!(create("../escape", &full_slots(), None, false).is_err());
        assert!(!home.dir.path().join("huebox").exists());
    }

    // -- create rules ----------------------------------------------------

    #[test]
    fn create_refuse_force_clash() {
        let home = Home::setup();
        let full = full_slots();
        create("ember", &full, None, false).unwrap();
        let mut edited = full.clone();
        edited.insert("background".to_owned(), "#010203".to_owned());
        assert!(create("ember", &edited, None, false)
            .unwrap_err()
            .0
            .contains("already exists"));
        assert_eq!(load("ember").unwrap().0, full); // untouched
        create_with("ember", &edited, None, true, "2026-10-01T12:00:00").unwrap();
        assert_eq!(load("ember").unwrap().0, edited);
        for name in ["Ember", "EMBER", "eMbEr"] {
            assert!(create(name, &full, None, false).is_err(), "{name}");
        }
        assert_eq!(list_themes()[0].0, "ember");
        let _ = home;
    }

    // -- state -----------------------------------------------------------

    #[test]
    fn state_pointer() {
        let home = Home::setup();
        assert_eq!(current(), None);
        set_current("ember").unwrap();
        assert_eq!(
            std::fs::read_to_string(state_path()).unwrap(),
            "current = \"ember\"\n"
        );
        assert_eq!(current().as_deref(), Some("ember"));
        for text in [
            "garbage",
            "current =",
            "current = [1]",
            "[theme]\ncurrent = 7\n",
            "current = \"bad name\"",
            "",
            "current = \"a/b\"",
        ] {
            std::fs::create_dir_all(super::home()).unwrap();
            std::fs::write(state_path(), text).unwrap();
            assert_eq!(current(), None, "{text:?}");
        }
        // The next set_current repairs a corrupt state…
        std::fs::write(state_path(), "]] not toml\n").unwrap();
        set_current("ember").unwrap();
        assert_eq!(current().as_deref(), Some("ember"));
        // …and a deleted theme leaves the pointer alone.
        create("ember", &full_slots(), None, false).unwrap();
        set_current("ember").unwrap();
        std::fs::remove_file(home.theme_file("ember")).unwrap();
        assert_eq!(current().as_deref(), Some("ember"));
        assert!(set_current("no/slashes").is_err());
    }

    // -- listing ---------------------------------------------------------

    #[test]
    fn listing_cases() {
        let home = Home::setup();
        let full = full_slots();
        for name in ["zinc", "amber", "ember"] {
            create(name, &full, None, false).unwrap();
        }
        let rows = list_themes();
        assert_eq!(
            rows.iter().map(|row| row.0.clone()).collect::<Vec<_>>(),
            vec!["amber", "ember", "zinc"]
        );
        for (name, path, mtime) in &rows {
            assert_eq!(*path, home.theme_file(name));
            assert!(*mtime > 0.0);
        }
        create("hand", &full, None, false).unwrap();
        assert_eq!(source_of("hand").unwrap(), "");
        // Stray files are not themes.
        home.dir.write("huebox/themes/notes.txt", "hi");
        home.dir.write("huebox/themes/.hidden.toml", "hi");
        home.dir.write("huebox/themes/ember.toml.tmp", "hi");
        home.dir.write("huebox/themes/a.toml", "hi"); // single legal name IS listed
        let names: Vec<String> = list_themes().iter().map(|row| row.0.clone()).collect();
        assert!(!names
            .iter()
            .any(|name| name == "notes" || name == ".hidden"));
        // The terminal is the import source.
        assert_eq!(read_terminal::<Mem>("ghostty", &home.config), full);
    }

    // -- push ------------------------------------------------------------

    fn push_opts(to: &str, path: &Path) -> PushOptions {
        PushOptions {
            to: vec![to.to_owned()],
            path: Some(path.to_path_buf()),
            ..PushOptions::default()
        }
    }

    #[test]
    fn push_lands_values_and_nothing_else() {
        let home = Home::setup();
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        edited.insert("palette-0".to_owned(), "#040506".to_owned());
        let result = push::<Mem>(&edited, &push_opts("ghostty", &home.config)).unwrap();
        assert!(!result.failed);
        assert_eq!(
            result.pushed,
            vec![("ghostty".to_owned(), home.config.clone())]
        );
        let after = std::fs::read_to_string(&home.config).unwrap();
        assert!(after.contains("background = #010203"));
        assert!(after.contains("palette = 0=#040506"));
        assert!(after.contains("# fixture config"));
        let before = ghostty_text(&full_slots());
        let changed: Vec<usize> = before
            .lines()
            .zip(after.lines())
            .enumerate()
            .filter(|(_, (a, b))| a != b)
            .map(|(i, _)| i)
            .collect();
        assert_eq!(changed.len(), 2);
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains("ghostty: pushed to")));
    }

    #[test]
    fn noop_push_keeps_bytes_and_mtime() {
        let home = Home::setup();
        let kitty = home.kitty();
        for (fmt, path) in [("ghostty", home.config.clone()), ("kitty", kitty)] {
            let slots = read_terminal::<Mem>(fmt, &path);
            let stamp = backdated(&path);
            let result = push::<Mem>(&slots, &push_opts(fmt, &path)).unwrap();
            assert!(!result.failed);
            assert_eq!(
                std::fs::metadata(&path).unwrap().modified().unwrap(),
                stamp,
                "{fmt} no-op push rewrote the config"
            );
        }
        // A second push of the same slots is byte-identical.
        let full = full_slots();
        push::<Mem>(&full, &push_opts("ghostty", &home.config)).unwrap();
        let before = std::fs::read_to_string(&home.config).unwrap();
        push::<Mem>(&full, &push_opts("ghostty", &home.config)).unwrap();
        assert_eq!(std::fs::read_to_string(&home.config).unwrap(), before);
    }

    /// Backend whose writer fails like a read-only config would.
    #[derive(Clone, Copy)]
    struct FailWrite;

    impl FormatStub for FailWrite {
        fn format_names() -> &'static [&'static str] {
            Mem::format_names()
        }
        fn default_paths(fmt: &str) -> Vec<String> {
            Mem::default_paths(fmt)
        }
        fn env_table(fmt: &str) -> &'static [(&'static str, &'static str)] {
            Mem::env_table(fmt)
        }
        fn read(fmt: &str, path: &Path) -> SlotMap {
            Mem::read(fmt, path)
        }
        fn write(_fmt: &str, _path: &Path, _slots: &SlotMap) -> std::io::Result<()> {
            Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "Denied",
            ))
        }
        fn color_line(fmt: &str, line: &str) -> bool {
            Mem::color_line(fmt, line)
        }
    }

    #[test]
    fn write_failure_reports_and_keeps_truth() {
        let home = Home::setup();
        // Truth first: the theme file is written before the push runs…
        create("ember", &full_slots(), None, false).unwrap();
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        save("ember", &edited, None).unwrap();
        // …so a failed push reports, and the truth stays as written.
        let result = push::<FailWrite>(&edited, &push_opts("ghostty", &home.config)).unwrap();
        assert!(result.failed);
        assert!(result.pushed.is_empty());
        assert!(result.lines.iter().any(|line| line.contains("Denied")));
        assert_eq!(
            std::fs::read_to_string(&home.config).unwrap(),
            ghostty_text(&full_slots())
        );
        assert_eq!(load("ember").unwrap().0, edited);
    }

    #[test]
    fn missing_keys_reported_never_inserted() {
        let home = Home::setup();
        let kitty = home.kitty();
        let mut slots = read_terminal::<Mem>("kitty", &kitty);
        slots.insert("background".to_owned(), "#010203".to_owned());
        let result = push::<Mem>(&slots, &push_opts("kitty", &kitty)).unwrap();
        assert!(!result.failed); // a report, not a failure
        let report = result.lines.join("\n");
        assert!(report.contains("not carried by this config:"), "{report}");
        assert!(report.contains("cursor-text"), "{report}");
        assert!(report.contains("palette-2"), "{report}");
        let after = std::fs::read_to_string(&kitty).unwrap();
        assert_eq!(after.lines().count(), kitty_text().lines().count());
        assert!(!after.contains("color2")); // no key invented
        assert!(after.contains("background            #010203"));
    }

    #[test]
    fn push_targeting_rules() {
        let home = Home::setup();
        let full = full_slots();
        // Explicit path with several targets is an error.
        let opts = PushOptions {
            to: vec!["ghostty".to_owned(), "kitty".to_owned()],
            path: Some(home.config.clone()),
            ..PushOptions::default()
        };
        assert!(push::<Mem>(&full, &opts).is_err());
        // A config without colours is never a target.
        let blank = home.dir.write("blank.ghostty", "font-size = 12\n");
        let result = push::<Mem>(&full, &push_opts("ghostty", &blank)).unwrap();
        assert!(result.failed);
        assert!(result.pushed.is_empty());
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains("no colours in")));
        assert_eq!(std::fs::read_to_string(&blank).unwrap(), "font-size = 12\n");
        // A target without a config is reported.
        let opts = PushOptions {
            to: vec!["alacritty".to_owned()],
            ..PushOptions::default()
        };
        let result = push::<Mem>(&full, &opts).unwrap();
        assert!(result.failed);
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains("no alacritty config with colours found")));
        // Unknown targets refused; blank `--to` needs a format.
        for to in [
            vec!["wezterm".to_owned()],
            vec!["kitty".to_owned(), "nope".to_owned()],
        ] {
            let opts = PushOptions {
                to,
                ..PushOptions::default()
            };
            assert!(push::<Mem>(&full, &opts)
                .unwrap_err()
                .0
                .contains("unknown format"));
        }
        let opts = PushOptions {
            to: vec![" , ".to_owned()],
            ..PushOptions::default()
        };
        assert!(push::<Mem>(&full, &opts)
            .unwrap_err()
            .0
            .contains("--to needs at least one format"));
        // no_push touches nothing.
        let before = std::fs::read_to_string(&home.config).unwrap();
        let stamp = backdated(&home.config);
        let mut edited = full.clone();
        edited.insert("background".to_owned(), "#010203".to_owned());
        let opts = PushOptions {
            to: vec!["ghostty".to_owned()],
            path: Some(home.config.clone()),
            no_push: true,
            ..PushOptions::default()
        };
        assert_eq!(push::<Mem>(&edited, &opts).unwrap(), PushResult::empty());
        assert_eq!(std::fs::read_to_string(&home.config).unwrap(), before);
        assert_eq!(
            std::fs::metadata(&home.config).unwrap().modified().unwrap(),
            stamp
        );
    }

    #[test]
    fn default_push_fans_out_and_skips_absent() {
        // No mocks: real XDG detection over a populated home.
        let dir = temp_dir("themes-fanout");
        let root = dir.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str()), ("HOME", root.as_str())]);
        let full = full_slots();
        let ghostty = dir.write("ghostty/config.ghostty", &ghostty_text(&full));
        let kitty = dir.write("kitty/kitty.conf", &kitty_text());
        let mut edited = full.clone();
        edited.insert("background".to_owned(), "#010203".to_owned());
        let result = push::<Mem>(&edited, &PushOptions::default()).unwrap();
        assert!(!result.failed);
        assert_eq!(
            result.pushed,
            vec![("ghostty".to_owned(), ghostty), ("kitty".to_owned(), kitty)]
        );
        // Terminals you do not have are skipped silently…
        std::fs::remove_file(dir.path().join("kitty/kitty.conf")).unwrap();
        let result = push::<Mem>(&edited, &PushOptions::default()).unwrap();
        assert!(!result.failed);
        assert_eq!(result.pushed.len(), 1);
        // …and none at all is a failure naming the registry.
        std::fs::remove_file(dir.path().join("ghostty/config.ghostty")).unwrap();
        let result = push::<Mem>(&full, &PushOptions::default()).unwrap();
        assert!(result.failed);
        assert!(result.pushed.is_empty());
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains("found no terminal config with colours")));
    }

    #[test]
    fn push_never_touches_truth() {
        // The preview-push bypass as a shape: push takes slots only, so a
        // dirty buffer reaches the terminal with truth untouched — the
        // themes dir does not even exist afterwards.
        let home = Home::setup();
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        let result = push::<Mem>(&edited, &push_opts("ghostty", &home.config)).unwrap();
        assert!(!result.failed);
        assert!(!home.dir.path().join("huebox").exists());
    }

    // -- ghostty native --------------------------------------------------------

    fn native_opts(home: &Home, main: &Path, name: &str) -> (PushOptions, PathBuf) {
        let native = home.dir.path().join("ghostty").join("themes").join(name);
        let opts = PushOptions {
            to: vec!["ghostty".to_owned()],
            path: Some(main.to_path_buf()),
            ghostty_native: true,
            name: Some(name.to_owned()),
            ..PushOptions::default()
        };
        (opts, native)
    }

    #[test]
    fn native_export_round_trips() {
        let home = Home::setup();
        let full = full_slots();
        let path = export_ghostty_native::<Mem>("ember", &full).unwrap();
        assert_eq!(
            path,
            home.dir.path().join("ghostty").join("themes").join("ember")
        );
        assert_eq!(read_terminal::<Mem>("ghostty", &path), full);
        let lines: Vec<String> = std::fs::read_to_string(&path)
            .unwrap()
            .lines()
            .map(str::to_owned)
            .collect();
        assert_eq!(lines[0], GHOSTTY_HEADER);
        assert_eq!(
            lines[1..17],
            (0..16)
                .map(|i| format!("palette = {i}={}", full[&format!("palette-{i}")]))
                .collect::<Vec<_>>()
        );
        assert_eq!(
            lines[17..],
            SLOTS[16..]
                .iter()
                .map(|slot| format!("{slot} = {}", full[*slot]))
                .collect::<Vec<_>>()
        );
        assert_eq!(lines.len(), 23);
        // A gap becomes the MISSING grey a push sends.
        let mut sparse = full.clone();
        sparse.remove("cursor-text");
        export_ghostty_native::<Mem>("ember", &sparse).unwrap();
        assert_eq!(
            read_terminal::<Mem>("ghostty", &path)["cursor-text"],
            MISSING
        );
    }

    #[test]
    fn native_push_exports_and_points() {
        let home = Home::setup();
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        let (opts, native) = native_opts(&home, &home.config, "ember");
        let result = push::<Mem>(&edited, &opts).unwrap();
        assert!(!result.failed);
        assert_eq!(result.pushed, vec![("ghostty".to_owned(), native.clone())]);
        let report = result.lines.join("\n");
        assert!(
            report.contains(&format!("ghostty: exported {}", native.display())),
            "{report}"
        );
        assert!(report.contains("theme = ember appended in"), "{report}");
        assert!(std::fs::read_to_string(&native)
            .unwrap()
            .contains("background = #010203"));
        let before: Vec<String> = ghostty_text(&full_slots())
            .lines()
            .map(str::to_owned)
            .collect();
        let after: Vec<String> = std::fs::read_to_string(&home.config)
            .unwrap()
            .lines()
            .map(str::to_owned)
            .collect();
        assert_eq!(after[..after.len() - 1], before);
        assert_eq!(after[after.len() - 1], "theme = ember");
    }

    #[test]
    fn native_push_rewrites_pointer_and_noops() {
        let home = Home::setup();
        let full = full_slots();
        let pointed = home.dir.write(
            "pointed.ghostty",
            "# mine\ntheme   = \"ember\"   # keep\nfont-size = 12\n",
        );
        home.dir
            .write("ghostty/themes/ember", "background = #101014\n");
        let (opts, _) = native_opts(&home, &pointed, "dusk");
        let result = push::<Mem>(&full, &opts).unwrap();
        assert!(!result.failed);
        assert_eq!(
            std::fs::read_to_string(&pointed).unwrap(),
            "# mine\ntheme   = \"dusk\"   # keep\nfont-size = 12\n"
        );
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains("ghostty: theme = dusk rewritten in")));
        // The theme file the pointer used to name is untouched.
        assert_eq!(
            std::fs::read_to_string(home.dir.path().join("ghostty/themes/ember")).unwrap(),
            "background = #101014\n"
        );
        // A no-op native push leaves the config alone.
        let (opts, _) = native_opts(&home, &home.config, "ember");
        push::<Mem>(&full, &opts).unwrap();
        let stamp = backdated(&home.config);
        let result = push::<Mem>(&full, &opts).unwrap();
        assert!(!result.failed);
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains("theme = ember unchanged")));
        assert_eq!(
            std::fs::metadata(&home.config).unwrap().modified().unwrap(),
            stamp
        );
    }

    #[test]
    fn push_without_name_edits_in_place() {
        let home = Home::setup();
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        let opts = PushOptions {
            to: vec!["ghostty".to_owned()],
            path: Some(home.config.clone()),
            ghostty_native: true, // forced, but a name is what an export is called
            ..PushOptions::default()
        };
        let result = push::<Mem>(&edited, &opts).unwrap();
        assert!(!result.failed);
        assert_eq!(
            result.pushed,
            vec![("ghostty".to_owned(), home.config.clone())]
        );
        assert!(std::fs::read_to_string(&home.config)
            .unwrap()
            .contains("background = #010203"));
        assert!(!home.dir.path().join("ghostty/themes/ember").exists());
        assert!(!std::fs::read_to_string(&home.config)
            .unwrap()
            .contains("theme ="));
        assert_eq!(result.overrides, None);
    }

    // -- overrides -----------------------------------------------------------

    fn main_with(home: &Home, text: &str) -> PathBuf {
        home.dir.write("ghostty/config", text)
    }

    #[test]
    fn overrides_read_and_clear() {
        let home = Home::setup();
        let main = main_with(
            &home,
            "background = #101014\n# foreground = #ffffff\nfont-size = 12\n",
        );
        assert_eq!(
            ghostty_overrides::<Mem>(Some(&main)),
            SlotMap::from([("background".to_owned(), "#101014".to_owned())])
        );
        assert!(ghostty_overrides::<Mem>(None).is_empty());
        assert!(ghostty_overrides::<Mem>(Some(&home.dir.path().join("missing"))).is_empty());
        // Clear removes only colour lines.
        let main = main_with(
            &home,
            "# keep me\nbackground = #101014  # mine\nfont-size = 12\npalette = 0=#000000\n",
        );
        assert_eq!(clear_ghostty_overrides::<Mem>(Some(&main)).unwrap(), 2);
        assert_eq!(
            std::fs::read_to_string(&main).unwrap(),
            "# keep me\nfont-size = 12\n"
        );
        assert!(ghostty_overrides::<Mem>(Some(&main)).is_empty());
        // …and is byte-identical (mtime kept) with nothing to remove.
        let main = main_with(&home, "# mine\nfont-size = 12\ntheme = ember\n");
        let stamp = backdated(&main);
        assert_eq!(clear_ghostty_overrides::<Mem>(Some(&main)).unwrap(), 0);
        assert_eq!(std::fs::metadata(&main).unwrap().modified().unwrap(), stamp);
        assert_eq!(
            std::fs::read_to_string(&main).unwrap(),
            "# mine\nfont-size = 12\ntheme = ember\n"
        );
        assert_eq!(clear_ghostty_overrides::<Mem>(None).unwrap(), 0);
    }

    #[test]
    fn native_push_reports_overrides() {
        let home = Home::setup();
        let full = full_slots();
        home.dir
            .write("ghostty/themes/Old", "background = #000000\n");
        let shadowed = main_with(
            &home,
            "background = #101014\nforeground = #e6e6ea\ntheme = Old\n",
        );
        let opts = PushOptions {
            to: vec!["ghostty".to_owned()],
            path: Some(shadowed.clone()),
            name: Some("ember".to_owned()),
            ..PushOptions::default()
        };
        let result = push::<Mem>(&full, &opts).unwrap();
        assert!(!result.failed);
        let (main, keys) = result.overrides.clone().unwrap();
        assert_eq!(main, shadowed);
        assert_eq!(keys, vec!["background".to_owned(), "foreground".to_owned()]);
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains("now shadowed")));
        // A clean export reports none.
        let clean = main_with(&home, "# mine\nfont-size = 12\ntheme = Old\n");
        let opts = PushOptions {
            to: vec!["ghostty".to_owned()],
            path: Some(clean),
            name: Some("ember".to_owned()),
            ..PushOptions::default()
        };
        let result = push::<Mem>(&full, &opts).unwrap();
        assert!(!result.failed);
        assert_eq!(result.overrides, None);
    }

    // -- theme-organised ghostty ---------------------------------------------

    struct Organised {
        _guard: crate::library::testutil::EnvGuard,
        dir: TestDir,
        config: PathBuf,
        old: PathBuf,
        old_before: String,
    }

    impl Organised {
        fn setup() -> Self {
            let dir = temp_dir("themes-organised");
            let root = dir.root();
            let guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str()), ("HOME", root.as_str())]);
            let old = dir.write("ghostty/themes/Nightspice", &ghostty_text(&full_slots()));
            let old_before = std::fs::read_to_string(&old).unwrap();
            let config = dir.write(
                "ghostty/config.ghostty",
                "# mine\nfont-size = 12\ntheme = Nightspice\n",
            );
            Organised {
                _guard: guard,
                dir,
                config,
                old,
                old_before,
            }
        }
    }

    #[test]
    fn organised_save_exports_and_repoints() {
        let home = Organised::setup();
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        let opts = PushOptions {
            to: vec!["ghostty".to_owned()],
            name: Some("test".to_owned()),
            ..PushOptions::default()
        };
        let result = push::<Mem>(&edited, &opts).unwrap();
        assert!(!result.failed);
        let exported = home.dir.path().join("ghostty").join("themes").join("test");
        assert_eq!(
            result.pushed,
            vec![("ghostty".to_owned(), exported.clone())]
        );
        assert_eq!(read_terminal::<Mem>("ghostty", &exported), edited);
        assert!(std::fs::read_to_string(&home.config)
            .unwrap()
            .contains("theme = test"));
        assert_eq!(std::fs::read_to_string(&home.old).unwrap(), home.old_before);
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains(&format!("ghostty: exported {}", exported.display()))));
    }

    #[test]
    fn organised_in_place_refused_for_foreign_file() {
        let home = Organised::setup();
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        let opts = PushOptions {
            to: vec!["ghostty".to_owned()],
            name: Some("test".to_owned()),
            ghostty_in_place: true,
            ..PushOptions::default()
        };
        let result = push::<Mem>(&edited, &opts).unwrap();
        assert!(result.failed);
        assert!(result.pushed.is_empty());
        assert_eq!(std::fs::read_to_string(&home.old).unwrap(), home.old_before);
        let report = result.lines.join("\n");
        assert!(
            report.contains("refusing to write theme 'test'"),
            "{report}"
        );
        assert!(report.contains("drop --ghostty-in-place"), "{report}");
    }

    // -- reload --------------------------------------------------------------

    use std::cell::RefCell;

    struct FakeReload {
        signals: RefCell<Vec<(u32, i32)>>,
        commands: RefCell<Vec<Vec<String>>>,
        signal_ok: bool,
        command_ok: bool,
    }

    impl FakeReload {
        fn new(signal_ok: bool, command_ok: bool) -> Self {
            FakeReload {
                signals: RefCell::new(Vec::new()),
                commands: RefCell::new(Vec::new()),
                signal_ok,
                command_ok,
            }
        }
    }

    impl ReloadBackend for FakeReload {
        fn send_signal(&self, pid: u32, sig: i32) -> bool {
            self.signals.borrow_mut().push((pid, sig));
            self.signal_ok
        }
        fn run_command(&self, argv: &[&str]) -> bool {
            self.commands
                .borrow_mut()
                .push(argv.iter().map(|s| (*s).to_owned()).collect());
            self.command_ok
        }
    }

    fn fake_proc_with(entries: &[(&str, &str, &[u8])]) -> TestDir {
        // (pid, file, bytes) rows under a fake /proc.
        let dir = temp_dir("themes-proc");
        for (pid, file, bytes) in entries {
            let path = dir.path().join(pid).join(file);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, bytes).unwrap();
        }
        dir
    }

    #[test]
    fn reload_signals_and_fallback() {
        let backend = FakeReload::new(true, true);
        // Ghostty: SIGUSR2 to the single-instance application.
        let proc = fake_proc_with(&[
            ("4242", "comm", b"ghostty\n"),
            ("4242", "cmdline", b"ghostty\x00--gtk-single-instance\x00"),
            ("7", "comm", b"ghostty\n"),
            ("7", "cmdline", b"ghostty\x00surface\x00"),
        ]);
        assert_eq!(
            reload_terminal_with("ghostty", &backend, proc.path()),
            "ghostty: reloaded (config re-read, pid 4242)"
        );
        assert_eq!(*backend.signals.borrow(), vec![(4242, SIGUSR2)]);
        // …and nothing when no application runs.
        let empty = temp_dir("themes-proc-empty");
        assert_eq!(reload_terminal_with("ghostty", &backend, empty.path()), "");
        // kitty: SIGUSR1 to $KITTY_PID.
        let _guard = lock_env(&[("KITTY_PID", "4242")]);
        assert_eq!(
            reload_terminal_with("kitty", &backend, empty.path()),
            "kitty: reloaded (config re-read, pid 4242)"
        );
        assert_eq!(
            *backend.signals.borrow(),
            vec![(4242, SIGUSR2), (4242, SIGUSR1)]
        );
        drop(_guard);
        // Missing or junk is None — all hermetic, never the developer shell.
        for value in ["", "junk", "0", "-5"] {
            let _guard = lock_env(&[("KITTY_PID", value)]);
            assert_eq!(env_pid("KITTY_PID"), None, "{value:?}");
        }
        let _guard = lock_env(&[("KITTY_PID", "4242")]);
        assert_eq!(env_pid("KITTY_PID"), Some(4242));
        drop(_guard);
        // Signal-first ordering: with $KITTY_PID set the remote control is
        // never attempted — no subprocess, no tty escape codes.
        let backend = FakeReload::new(true, true);
        let _guard = lock_env(&[("KITTY_PID", "4242")]);
        assert_eq!(
            reload_terminal_with("kitty", &backend, empty.path()),
            "kitty: reloaded (config re-read, pid 4242)"
        );
        assert_eq!(*backend.signals.borrow(), vec![(4242, SIGUSR1)]);
        assert!(backend.commands.borrow().is_empty());
        drop(_guard);
        // …and a failed signal is the same best-effort miss, still with no
        // subprocess attempted.
        let backend = FakeReload::new(false, true);
        let _guard = lock_env(&[("KITTY_PID", "4242")]);
        assert_eq!(reload_terminal_with("kitty", &backend, empty.path()), "");
        assert_eq!(*backend.signals.borrow(), vec![(4242, SIGUSR1)]);
        assert!(backend.commands.borrow().is_empty());
        drop(_guard);
    }

    #[test]
    fn ghostty_app_pick_shared_by_both_platforms() {
        // Surfaces carry no --gtk-single-instance and never win, whatever
        // listed them (/proc walk or pgrep/ps).
        let rows = vec![
            (7u32, "ghostty".to_owned(), "ghostty surface".to_owned()),
            (
                4242u32,
                "ghostty".to_owned(),
                "ghostty --gtk-single-instance".to_owned(),
            ),
            (
                99u32,
                "other".to_owned(),
                "ghostty --gtk-single-instance".to_owned(),
            ),
        ];
        assert_eq!(pick_ghostty_app(rows), Some(4242));
        assert_eq!(
            pick_ghostty_app(vec![(
                7u32,
                "ghostty".to_owned(),
                "ghostty surface".to_owned()
            )]),
            None
        );
        assert_eq!(pick_ghostty_app(Vec::new()), None);
        // Lowest pid wins when two applications somehow run.
        let rows = vec![
            (
                9000u32,
                "ghostty".to_owned(),
                "ghostty --gtk-single-instance".to_owned(),
            ),
            (
                100u32,
                "ghostty".to_owned(),
                "ghostty --gtk-single-instance".to_owned(),
            ),
        ];
        assert_eq!(pick_ghostty_app(rows), Some(100));
    }

    #[test]
    fn ghostty_app_macos_rows_from_pgrep_ps() {
        use std::collections::HashMap;
        // Fake macOS: pgrep lists candidates, ps confirms each one.
        let table: HashMap<String, String> = [
            ("pgrep -x ghostty", "4242\n7\n"),
            ("ps -p 4242 -o args=", "ghostty --gtk-single-instance\n"),
            ("ps -p 7 -o args=", "/Applications/Ghostty.app/surface\n"),
        ]
        .iter()
        .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
        .collect();
        let run = |argv: &[&str]| table.get(&argv.join(" ")).cloned();
        let rows = ghostty_app_rows_macos(&run);
        assert_eq!(rows.len(), 2);
        assert_eq!(pick_ghostty_app(rows), Some(4242));
        // pgrep missing (or no match): no rows, no pid — never an error.
        let run = |_: &[&str]| None;
        assert!(ghostty_app_rows_macos(&run).is_empty());
        assert_eq!(pick_ghostty_app(ghostty_app_rows_macos(&run)), None);
        // Junk pids in the listing are skipped, not fatal.
        let table: HashMap<String, String> = [
            ("pgrep -x ghostty", "junk\n4242\n"),
            ("ps -p 4242 -o args=", "ghostty --gtk-single-instance\n"),
        ]
        .iter()
        .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
        .collect();
        let run = |argv: &[&str]| table.get(&argv.join(" ")).cloned();
        assert_eq!(pick_ghostty_app(ghostty_app_rows_macos(&run)), Some(4242));
    }

    #[test]
    fn alacritty_never_signalled_nor_advised() {
        // No signal, no subprocess: live_config_reload is on by default.
        let backend = FakeReload::new(true, true);
        let proc = temp_dir("themes-proc-alacritty");
        assert_eq!(reload_terminal_with("alacritty", &backend, proc.path()), "");
        assert!(backend.signals.borrow().is_empty());
        assert!(backend.commands.borrow().is_empty());
        assert!(!RELOAD_COMMANDS.iter().any(|(name, _)| *name == "alacritty"));
        // …and no advice line for alacritty-only pushes.
        let alone = PushResult {
            lines: Vec::new(),
            pushed: vec![("alacritty".to_owned(), PathBuf::from("/tmp/alacritty.toml"))],
            failed: false,
            reloaded: Vec::new(),
            overrides: None,
        };
        assert!(!alone.needs_reload_advice());
        // A pushed-but-unreloaded ghostty still owes the line…
        let ghostty = PushResult {
            pushed: vec![("ghostty".to_owned(), PathBuf::from("/tmp/config"))],
            ..alone.clone()
        };
        assert!(ghostty.needs_reload_advice());
        // …unless it reloaded, and nothing pushed owes nothing.
        let reloaded = PushResult {
            reloaded: vec!["ghostty".to_owned()],
            ..ghostty.clone()
        };
        assert!(!reloaded.needs_reload_advice());
        assert!(!PushResult::empty().needs_reload_advice());
        // Mixed targets: one live-reloading terminal does not excuse the other.
        let mixed = PushResult {
            pushed: vec![
                ("alacritty".to_owned(), PathBuf::from("/tmp/alacritty.toml")),
                ("ghostty".to_owned(), PathBuf::from("/tmp/config")),
            ],
            ..alone.clone()
        };
        assert!(mixed.needs_reload_advice());
    }

    #[test]
    fn kitty_fallback_needs_a_kitty_to_answer() {
        let backend = FakeReload::new(true, true);
        let me = std::process::id();
        // Inside kitty (a kitty ancestor): remote control runs, tty-free.
        let me_string = me.to_string();
        let proc = fake_proc_with(&[
            (
                &me_string,
                "stat",
                &format!("{me} (test) R 4243 0 0").into_bytes(),
            ),
            ("4243", "comm", b"kitty\n"),
        ]);
        let _guard = lock_env(&[("KITTY_PID", ""), ("KITTY_LISTEN_ON", "")]);
        assert!(in_kitty(proc.path(), me));
        assert_eq!(
            reload_terminal_with("kitty", &backend, proc.path()),
            "kitty: reloaded (kitty @ load-config)"
        );
        assert_eq!(
            backend.commands.borrow()[0],
            vec!["kitty".to_owned(), "@".to_owned(), "load-config".to_owned()]
        );
        drop(_guard);
        // Outside kitty: no tty fallback (a doomed wait otherwise).
        let me_string = me.to_string();
        let stat = format!("{me} (test) R 1 0 0").into_bytes();
        let proc = fake_proc_with(&[(&me_string, "stat", &stat)]);
        let _guard = lock_env(&[("KITTY_PID", ""), ("KITTY_LISTEN_ON", "")]);
        assert!(!in_kitty(proc.path(), me));
        let before = backend.commands.borrow().len();
        assert_eq!(reload_terminal_with("kitty", &backend, proc.path()), "");
        assert_eq!(backend.commands.borrow().len(), before);
        drop(_guard);
        // Alacritty has no reload interface (it live-reloads): empty here,
        // and no advice line either (see needs_reload_advice).
        assert_eq!(reload_terminal_with("alacritty", &backend, proc.path()), "");
    }

    #[test]
    fn successful_push_reloads_only_what_it_pushed() {
        let home = Home::setup();
        let proc = fake_proc_with(&[
            ("4242", "comm", b"ghostty\n"),
            ("4242", "cmdline", b"ghostty\x00--gtk-single-instance\x00"),
        ]);
        let backend = FakeReload::new(true, true);
        let mut edited = full_slots();
        edited.insert("background".to_owned(), "#010203".to_owned());
        let opts = PushOptions {
            to: vec!["ghostty".to_owned()],
            path: Some(home.config.clone()),
            name: Some("ember".to_owned()),
            reload: true,
            ..PushOptions::default()
        };
        let result = push_with::<Mem, FakeReload>(&edited, &opts, &backend, proc.path()).unwrap();
        assert_eq!(result.reloaded, vec!["ghostty".to_owned()]);
        assert!(result
            .lines
            .iter()
            .any(|line| line.contains("ghostty: reloaded")));
        assert_eq!(*backend.signals.borrow(), vec![(4242, SIGUSR2)]);
    }

    // -- ramp ----------------------------------------------------------------

    #[test]
    fn ramp_covers_every_slot() {
        let mut keys: Vec<&str> = RAMP.iter().map(|(slot, _)| *slot).collect();
        keys.sort();
        let mut expected = SLOTS.to_vec();
        expected.sort();
        assert_eq!(keys, expected);
        for (_, value) in RAMP {
            assert_eq!(*value, value.to_lowercase());
            assert_eq!(value.len(), 7);
            assert!(crate::library::is_hex(value), "{value}");
        }
        assert_eq!(ramp_slots().len(), 22);
    }
}
