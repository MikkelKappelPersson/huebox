//! Finding the terminal you are in and the config holding its colours.
//!
//! Rust port of `huebox/detect.py`. Finding a config is only half of it:
//! [`ensure_theme_pointer`] writes the one line deciding *which* file a
//! Ghostty config reads, with the same line-level discipline every other
//! write to somebody else's file follows. [`resolve`] is the facade for
//! finding the push target.

use std::path::{Path, PathBuf};

use crate::format::FormatStub;

/// `~` expansion against `HOME` (empty/unset home leaves the path as-is).
pub fn expanduser(path: &str) -> String {
    if let Some(rest) = path.strip_prefix('~') {
        if rest.is_empty() || rest.starts_with('/') {
            if let Ok(home) = std::env::var("HOME") {
                if !home.is_empty() {
                    return format!("{home}{rest}");
                }
            }
        }
    }
    path.to_owned()
}

/// `$XDG_CONFIG_HOME`, falling back to `~/.config` (empty counts as unset,
/// as Python's `or` does).
pub fn config_home() -> PathBuf {
    match std::env::var("XDG_CONFIG_HOME") {
        Ok(base) if !base.is_empty() => PathBuf::from(base),
        _ => PathBuf::from(expanduser("~/.config")),
    }
}

/// Put the XDG location first so tests and sandboxes are respected.
/// Mirrors `_prefer_xdg`: for `~/.config/...` entries the expanded form
/// comes first, then the original spelling.
pub fn prefer_xdg(paths: &[String], base: &Path) -> Vec<String> {
    let mut out = Vec::new();
    for path in paths {
        if let Some(rest) = path.strip_prefix("~/.config/") {
            out.push(base.join(rest).to_string_lossy().into_owned());
        }
        out.push(path.clone());
    }
    out
}

/// Candidate paths for `fmt`: env probes first (in table order), then the
/// XDG-preferred defaults. Only files on disk are yielded.
pub fn candidate_paths<F: FormatStub>(fmt: &str) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for (var, filename) in F::env_table(fmt) {
        let value = std::env::var(var).unwrap_or_default();
        if value.is_empty() {
            continue;
        }
        let value_path = Path::new(&value);
        let base = if value_path.is_dir() {
            value.clone()
        } else {
            match value_path
                .parent()
                .map(|parent| parent.to_string_lossy().into_owned())
            {
                Some(parent) if !parent.is_empty() => parent,
                _ => ".".to_owned(),
            }
        };
        let candidate = if filename.is_empty() {
            value.clone()
        } else {
            Path::new(&base)
                .join(filename)
                .to_string_lossy()
                .into_owned()
        };
        if Path::new(&candidate).is_file() {
            out.push(PathBuf::from(candidate));
        }
    }
    let home = config_home();
    let mut defaults = prefer_xdg(&F::default_paths(fmt), &home);
    if fmt == "kitty" {
        // The XDG root config leads, as `detect.py` prepends at import.
        // Computed at call time (not import time) so a temporary home is
        // respected; the order matches Python's table.
        defaults.insert(
            0,
            home.join("kitty/kitty.conf").to_string_lossy().into_owned(),
        );
    }
    for candidate in defaults {
        let expanded = expanduser(&candidate);
        if Path::new(&expanded).is_file() {
            out.push(PathBuf::from(expanded));
        }
    }
    out
}

// ---------------------------------------------------------------------------
// probes
// ---------------------------------------------------------------------------

/// Terminals to try, most trustworthy first: per-window variables beat
/// `TERM_PROGRAM`, because launchers leak stale values into the env.
/// `names` is the backend's registry order (the `TERM_PROGRAM` fallback
/// and the remainder follow it).
pub fn detect_order_in(registry: &[&str]) -> Vec<String> {
    let probe = |vars: &[&str]| {
        vars.iter().any(|var| match std::env::var(var) {
            Ok(value) => !value.is_empty(),
            Err(_) => false,
        })
    };
    let mut names: Vec<String> = Vec::new();
    let mut push = |name: &str| {
        if !names.iter().any(|seen| seen == name) {
            names.push(name.to_owned());
        }
    };
    if probe(&["GHOSTTY_RESOURCES_DIR"]) {
        push("ghostty");
    }
    if probe(&["KITTY_WINDOW_ID", "KITTY_LISTEN_ON"]) {
        push("kitty");
    }
    if probe(&["ALACRITTY_WINDOW_ID", "ALACRITTY_SOCKET"]) {
        push("alacritty");
    }
    if probe(&["WEZTERM_EXECUTABLE", "WEZTERM_PANE"]) {
        push("wezterm");
    }
    let program = std::env::var("TERM_PROGRAM")
        .unwrap_or_default()
        .to_lowercase();
    // Registry order is the tie-break; the real order comes from the
    // backend so the stub and the real format agree.
    for name in registry {
        if program.contains(name) {
            push(name);
        }
    }
    for name in registry {
        push(name);
    }
    names
}

/// [`detect_order_in`] over the backend's registry.
pub fn detect_order<F: FormatStub>() -> Vec<String> {
    detect_order_in(F::format_names())
}

// ---------------------------------------------------------------------------
// Ghostty config-file includes and theme = pointer (byte-level)
// ---------------------------------------------------------------------------

fn is_comment_line(line: &[u8]) -> bool {
    let mut i = 0;
    while i < line.len() && matches!(line[i], b' ' | b'\t' | b'\r' | b'\x0b' | b'\x0c') {
        i += 1;
    }
    line.get(i) == Some(&b'#')
}

fn skip_ws(line: &[u8], mut i: usize) -> usize {
    while i < line.len() && matches!(line[i], b' ' | b'\t' | b'\r' | b'\x0b' | b'\x0c') {
        i += 1;
    }
    i
}

/// Match `key = value` with Ghostty's quoting rule: the value is quoted
/// (any chars but the quote) or bare — running to the first `#`, never
/// ending in a space. Returns byte ranges `(pre_end, value_start,
/// value_end)` plus the quote char when quoted.
///
/// `pre` is `line[..pre_end]`… more precisely the rewrite needs
/// `line[..value_quote_start]`; callers rebuild from the ranges.
struct Assignment {
    /// End of `pre` (`\s*key\s*=\s*`, quotes excluded).
    pre_end: usize,
    /// Start of the value content (after the opening quote, if any).
    value_start: usize,
    /// End of the value content (before the closing quote, if any).
    value_end: usize,
    /// End of the closing quote (== value_end when bare).
    token_end: usize,
    /// The quote char, when the value was quoted.
    quote: Option<u8>,
}

fn match_assignment(line: &[u8], key: &[u8]) -> Option<Assignment> {
    let mut i = skip_ws(line, 0);
    if line.get(i..i + key.len()) != Some(key) {
        return None;
    }
    i += key.len();
    i = skip_ws(line, i);
    if line.get(i) != Some(&b'=') {
        return None;
    }
    i = skip_ws(line, i + 1);
    let pre_end = i;
    let (value_start, value_end, token_end, quote) =
        if matches!(line.get(i), Some(b'"') | Some(b'\'')) {
            let quote = line[i];
            let start = i + 1;
            let mut j = start;
            while j < line.len() && line[j] != quote {
                j += 1;
            }
            if j >= line.len() {
                return None; // unterminated quote
            }
            (start, j, j + 1, Some(quote))
        } else {
            // Bare: to the first `#` (or end — a `\r` ends the value
            // too, as `splitlines` never yields one), trailing spaces
            // excluded. The token ends where the value does, so the
            // rewrite keeps the spacing before a trailing comment.
            let mut j = i;
            while j < line.len() && line[j] != b'#' && line[j] != b'\r' {
                j += 1;
            }
            let mut end = j;
            while end > i && matches!(line[end - 1], b' ' | b'\t') {
                end -= 1;
            }
            (i, end, end, None)
        };
    // Post: spaces, an optional comment, an optional \r, then end.
    let mut k = token_end;
    while k < line.len() && matches!(line[k], b' ' | b'\t') {
        k += 1;
    }
    if line.get(k) == Some(&b'#') {
        k = line.len(); // a comment runs to the line end
    }
    if line.get(k) == Some(&b'\r') {
        k += 1;
    }
    if k != line.len() {
        return None;
    }
    Some(Assignment {
        pre_end,
        value_start,
        value_end,
        token_end,
        quote,
    })
}

/// Config files pulled in via `config-file = ?path`.
///
/// A trailing comment is a comment, `?path` is relative to this config,
/// and an include not on disk is skipped rather than reported.
pub fn ghostty_includes(config_path: &Path) -> Vec<PathBuf> {
    let bytes = match std::fs::read(config_path) {
        Ok(bytes) => bytes,
        Err(_) => return Vec::new(),
    };
    let directory = config_path
        .parent()
        .map(|parent| std::path::absolute(parent).unwrap_or_else(|_| parent.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));
    let mut out = Vec::new();
    for line in bytes.split(|b| *b == b'\n') {
        // splitlines strips a trailing \r; do the same before matching.
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if is_comment_line(line) {
            continue;
        }
        let hit = match match_assignment(line, b"config-file") {
            Some(hit) => hit,
            None => continue,
        };
        let raw = String::from_utf8_lossy(&line[hit.value_start..hit.value_end]);
        let target = raw
            .replace(
                '?',
                &format!(
                    "{}{}",
                    directory.to_string_lossy(),
                    std::path::MAIN_SEPARATOR
                ),
            )
            .replace('~', &std::env::var("HOME").unwrap_or_default());
        if Path::new(&target).is_file() {
            out.push(PathBuf::from(target));
        }
    }
    out
}

/// `$XDG_CONFIG_HOME/ghostty/themes` — where `theme = Name` resolves.
/// Read at call time so a temporary home is respected.
pub fn ghostty_themes_dir() -> PathBuf {
    config_home().join("ghostty").join("themes")
}

/// The Ghostty config itself, with `theme =` and includes *not* followed.
///
/// The first candidate path that exists — or the explicit config the user
/// named, which is the file they mean by definition.
pub fn ghostty_main_config<F: FormatStub>(path: Option<&Path>) -> Option<PathBuf> {
    if let Some(explicit) = path {
        let expanded = expanduser(&explicit.to_string_lossy());
        return if Path::new(&expanded).is_file() {
            Some(PathBuf::from(expanded))
        } else {
            None
        };
    }
    candidate_paths::<F>("ghostty").into_iter().next()
}

/// The theme `config_path`'s last `theme =` line names, or `None`.
///
/// The name a config is *on*, with no opinion about whether the file
/// behind it exists. A missing/unreadable config is on no theme.
pub fn ghostty_theme_name(config_path: &Path) -> Option<String> {
    let bytes = std::fs::read(config_path).ok()?;
    let mut name: Option<String> = None;
    for line in bytes.split(|b| *b == b'\n') {
        if is_comment_line(line) {
            continue;
        }
        if let Some(hit) = match_assignment(line, b"theme") {
            name =
                Some(String::from_utf8_lossy(&line[hit.value_start..hit.value_end]).into_owned());
        }
    }
    match name {
        Some(name) if !name.is_empty() => Some(name),
        _ => None,
    }
}

/// Follow `theme = Name` to the file holding the colours.
///
/// A read-only shipped theme is copied where we may edit it, as in
/// `detect.py`. A copy failure reads as "no theme file" (defensive:
/// `resolve` must never fail a save's target search on a permissions
/// edge — the push reports colourless instead).
pub fn ghostty_theme_file(config_path: &Path) -> Option<PathBuf> {
    let name = ghostty_theme_name(config_path)?;
    let user = ghostty_themes_dir().join(&name);
    if user.is_file() {
        return Some(user);
    }
    let resources =
        std::env::var("GHOSTTY_RESOURCES_DIR").unwrap_or_else(|_| "/usr/share/ghostty".to_owned());
    let shipped = Path::new(&resources).join("themes").join(&name);
    if shipped.is_file() {
        if let Some(parent) = user.parent() {
            if std::fs::create_dir_all(parent).is_err() {
                return None;
            }
        }
        let mode = std::fs::metadata(&shipped)
            .ok()
            .map(|meta| meta.permissions());
        if std::fs::copy(&shipped, &user).is_err() {
            return None;
        }
        if let Some(mode) = mode {
            let _ = std::fs::set_permissions(&user, mode);
        }
        return Some(user);
    }
    None
}

/// What pointing `config_path` at `name` did. Mirrors the
/// `unchanged` / `rewritten` / `appended` report words.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PointerAction {
    /// Already pointing at `name`: the file was not rewritten (mtime kept).
    Unchanged,
    /// An existing `theme =` kept its spacing/quotes/comment; only the
    /// value moved.
    Rewritten,
    /// No theme line: one appended, nothing else moved.
    Appended,
}

impl PointerAction {
    /// The report word `push` prints.
    pub fn as_str(self) -> &'static str {
        match self {
            PointerAction::Unchanged => "unchanged",
            PointerAction::Rewritten => "rewritten",
            PointerAction::Appended => "appended",
        }
    }
}

/// Point `config_path` at the Ghostty theme file `name`: swap or append
/// the one `theme =` line, every other byte identical.
///
/// `newline=""` semantics: the file is read and written as raw bytes, so
/// CRLF configs and non-UTF-8 bytes round-trip untouched. A config
/// already pointing at `name` is not rewritten (mtime survives a no-op).
/// A name that would break the file into two lines or two keys is
/// refused; a missing config raises instead of inventing one.
pub fn ensure_theme_pointer(config_path: &Path, name: &str) -> std::io::Result<PointerAction> {
    if name.is_empty()
        || name
            .bytes()
            .any(|c| matches!(c, b'"' | b'\'' | b'#' | b'\r' | b'\n'))
    {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("not a usable theme name: '{name}'"),
        ));
    }
    let bytes = std::fs::read(config_path)?;
    let lines: Vec<&[u8]> = bytes.split(|b| *b == b'\n').collect();
    let mut index = None;
    for (position, line) in lines.iter().enumerate() {
        if is_comment_line(line) {
            continue;
        }
        if match_assignment(line, b"theme").is_some() {
            index = Some(position); // a later key wins, as Ghostty reads
        }
    }
    if let Some(position) = index {
        let hit =
            match_assignment(lines[position], b"theme").expect("re-matched line must still match");
        if &lines[position][hit.value_start..hit.value_end] == name.as_bytes() {
            return Ok(PointerAction::Unchanged);
        }
        let quote = hit.quote.map(|q| vec![q]).unwrap_or_default();
        let mut swapped = Vec::new();
        swapped.extend_from_slice(&lines[position][..hit.pre_end]);
        if hit.quote.is_some() {
            swapped.extend_from_slice(&quote);
        }
        swapped.extend_from_slice(name.as_bytes());
        if hit.quote.is_some() {
            swapped.extend_from_slice(&quote);
        }
        // Post: everything after the value's closing quote…
        swapped.extend_from_slice(&lines[position][hit.token_end..]);
        let mut owned: Vec<Vec<u8>> = lines.iter().map(|line| line.to_vec()).collect();
        owned[position] = swapped;
        std::fs::write(config_path, join_lines(&owned))?;
        return Ok(PointerAction::Rewritten);
    }
    // …else append, keeping the file's own line ending.
    let ending: &[u8] = if bytes.windows(2).any(|pair| pair == b"\r\n".as_slice()) {
        b"\r"
    } else {
        b""
    };
    let mut appended = b"theme = ".to_vec();
    appended.extend_from_slice(name.as_bytes());
    appended.extend_from_slice(ending);
    let mut owned: Vec<Vec<u8>> = lines.iter().map(|line| line.to_vec()).collect();
    if owned.last().is_some_and(|last| last.is_empty()) {
        let at = owned.len() - 1; // the final "" is the newline: keep it
        owned.insert(at, appended);
    } else {
        owned.push(appended); // ended without one
    }
    std::fs::write(config_path, join_lines(&owned))?;
    Ok(PointerAction::Appended)
}

/// Join split-`\n` lines back with `\n` (the inverse of the split every
/// byte-level writer starts from).
fn join_lines(lines: &[Vec<u8>]) -> Vec<u8> {
    let mut out = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        if i > 0 {
            out.push(b'\n');
        }
        out.extend_from_slice(line);
    }
    out
}

/// Does `path` define colours — directly or through what it pulls in?
/// The main Ghostty config may hold none of its own when it points at a
/// theme file, and still be a real terminal config.
pub fn config_holds_colours<F: FormatStub>(fmt: &str, path: &Path) -> bool {
    if !F::read(fmt, path).is_empty() {
        return true;
    }
    if fmt == "ghostty" {
        for included in ghostty_includes(path) {
            if !F::read(fmt, &included).is_empty() {
                return true;
            }
        }
        if let Some(themed) = ghostty_theme_file(path) {
            if !F::read(fmt, &themed).is_empty() {
                return true;
            }
        }
    }
    false
}

/// Which format a `--config` path belongs to.
///
/// A known file name wins; for anything else the file decides, and a tie
/// (or an empty file) is not inferable.
pub fn infer_format<F: FormatStub>(path: &Path) -> Option<String> {
    let name = path.file_name()?.to_string_lossy().to_lowercase();
    for fmt in F::format_names() {
        for candidate in F::default_paths(fmt) {
            let base = candidate
                .rsplit('/')
                .next()
                .unwrap_or(&candidate)
                .to_lowercase();
            if base == name {
                return Some(fmt.to_string());
            }
        }
    }
    let mut best = 0;
    let mut winners = Vec::new();
    for fmt in F::format_names() {
        let count = F::read(fmt, path).len();
        if count > best {
            best = count;
            winners.clear();
            winners.push(fmt.to_string());
        } else if count == best && count > 0 {
            winners.push(fmt.to_string());
        }
    }
    if best == 0 || winners.len() != 1 {
        return None;
    }
    winners.into_iter().next()
}

/// First config for `fmt` that actually defines colours.
fn resolve_for<F: FormatStub>(fmt: &str) -> Option<PathBuf> {
    for candidate in candidate_paths::<F>(fmt) {
        let mut target = candidate;
        if fmt == "ghostty" {
            for included in ghostty_includes(&target) {
                if !F::read(fmt, &included).is_empty() {
                    return Some(included);
                }
            }
            if let Some(themed) = ghostty_theme_file(&target) {
                target = themed;
            }
        }
        if !F::read(fmt, &target).is_empty() {
            return Some(target);
        }
    }
    None
}

/// Return (format, config_path, error): the facade for finding the push
/// target. Unknown names in the default order (e.g. `wezterm`, which has
/// no format) are skipped, never fatal — the loop answers "which config
/// holds colours", and a probed terminal without one is not an answer.
pub fn resolve<F: FormatStub>(
    fmt: Option<&str>,
    path: Option<&Path>,
) -> (Option<String>, Option<PathBuf>, Option<String>) {
    if let Some(wanted) = fmt {
        if !F::format_names().contains(&wanted) {
            return (None, None, Some(format!("unknown format '{wanted}'")));
        }
    }
    if let Some(explicit) = path {
        let expanded = expanduser(&explicit.to_string_lossy());
        if !Path::new(&expanded).is_file() {
            return (
                fmt.map(str::to_owned),
                None,
                Some(format!("no such file: {expanded}")),
            );
        }
        let found = fmt
            .map(str::to_owned)
            .or_else(|| infer_format::<F>(Path::new(&expanded)));
        return (found, Some(PathBuf::from(expanded)), None);
    }
    let order: Vec<String> = match fmt {
        Some(wanted) => vec![wanted.to_owned()],
        None => detect_order::<F>(),
    };
    for name in order {
        if !F::format_names().contains(&name.as_str()) {
            continue;
        }
        if let Some(found) = resolve_for::<F>(&name) {
            return (Some(name), Some(found), None);
        }
    }
    if let Some(wanted) = fmt {
        return (
            Some(wanted.to_owned()),
            None,
            Some(format!("no {wanted} config with colours found")),
        );
    }
    (
        None,
        None,
        Some(format!(
            "found no terminal config with colours; pass --format with one of {}",
            F::format_names().join(", ")
        )),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::providers::tests::Fake;
    use crate::testutil::{lock_env, temp_dir};

    const GHOSTTY: &str = "background = #101014\n";
    const KITTY: &str = "background  #101014\n";

    fn write_file(path: &Path, text: &str) -> PathBuf {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, text).unwrap();
        path.to_path_buf()
    }

    fn read_raw(path: &Path) -> Vec<u8> {
        std::fs::read(path).unwrap()
    }

    // -- env probes ------------------------------------------------------

    #[test]
    fn kitty_env_spellings() {
        let home = temp_dir("detect-kitty-env");
        let new = home.path().join("kitty-new");
        std::fs::create_dir_all(&new).unwrap();
        write_file(&new.join("kitty.conf"), KITTY);
        let old = home.path().join("kitty-old");
        std::fs::create_dir_all(&old).unwrap();
        write_file(&old.join("kitty.conf"), KITTY);

        let _guard = lock_env(&[("KITTY_CONFIG_DIRECTORY", new.to_str().unwrap())]);
        // Documented spelling probed first.
        assert_eq!(candidate_paths::<Fake>("kitty")[0], new.join("kitty.conf"));
        drop(_guard);

        // The old spelling still works for one release.
        let _guard = lock_env(&[("KITTY_CONFIG_DIR", old.to_str().unwrap())]);
        assert!(candidate_paths::<Fake>("kitty").contains(&old.join("kitty.conf")));
        drop(_guard);

        // Documented wins when both are set.
        let _guard = lock_env(&[
            ("KITTY_CONFIG_DIRECTORY", new.to_str().unwrap()),
            ("KITTY_CONFIG_DIR", old.to_str().unwrap()),
        ]);
        assert_eq!(candidate_paths::<Fake>("kitty")[0], new.join("kitty.conf"));
        drop(_guard);

        // A var pointing at the file itself works too.
        let file = new.join("kitty.conf");
        let _guard = lock_env(&[("KITTY_CONFIG_DIRECTORY", file.to_str().unwrap())]);
        assert_eq!(candidate_paths::<Fake>("kitty")[0], file);
    }

    #[test]
    fn alacritty_has_no_env_probe() {
        // Pure table check first — no env needed, and two live guards
        // on one thread would deadlock the serialiser.
        assert!(Fake::env_table("alacritty").is_empty());
        let home = temp_dir("detect-alacritty-env");
        let path = write_file(&home.path().join("alacritty.toml"), "[x]\n");
        let _guard = lock_env(&[
            ("ALACRITTY_CONFIG", home.path().to_str().unwrap()),
            ("ALACRITTY_CONFIG_DIR", home.path().to_str().unwrap()),
        ]);
        assert!(!candidate_paths::<Fake>("alacritty").contains(&path));
    }

    #[test]
    fn detect_order_probes_before_program() {
        let _guard = lock_env(&[("KITTY_WINDOW_ID", "1")]);
        assert_eq!(detect_order::<Fake>()[0], "kitty");
        drop(_guard);
        let _guard = lock_env(&[("TERM_PROGRAM", "ghostty")]);
        assert_eq!(detect_order::<Fake>()[0], "ghostty");
        drop(_guard);
        let _guard = lock_env(&[]);
        assert_eq!(
            detect_order::<Fake>(),
            vec!["ghostty", "kitty", "alacritty"]
        );
    }

    // -- search order ----------------------------------------------------

    #[test]
    fn xdg_search_order() {
        // Pure table checks first — no env needed (see above).
        // `_prefer_xdg` puts the XDG location first, original spelling kept.
        let base = PathBuf::from("/tmp/hb-xdg");
        let ordered = prefer_xdg(
            &[
                String::from("~/.config/alacritty.toml"),
                String::from("~/.alacritty.toml"),
            ],
            &base,
        );
        assert_eq!(
            ordered,
            vec![
                "/tmp/hb-xdg/alacritty.toml".to_owned(),
                "~/.config/alacritty.toml".to_owned(),
                "~/.alacritty.toml".to_owned(),
            ]
        );
        // Raw tables keep upstream's own order with the legacy paths.
        assert_eq!(
            Fake::default_paths("alacritty"),
            vec![
                "~/.config/alacritty/alacritty.toml".to_owned(),
                "~/.config/alacritty.toml".to_owned(),
                "~/.config/alacritty/alacritty.yml".to_owned(),
                "~/.alacritty.toml".to_owned(),
            ]
        );
        // …and the XDG root config is actually read through them.
        let home = temp_dir("detect-xdg-read");
        let path = home.write(
            "alacritty.toml",
            "colors.primary.background = \"#0f0f1a\"\n",
        );
        let root = home.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str())]);
        let ordered = prefer_xdg(&Fake::default_paths("alacritty"), &config_home());
        // `~/.config/alacritty.toml` prefers to `$XDG/alacritty.toml`
        // (position 2: after the first entry and its original spelling).
        assert_eq!(ordered[2], path.to_string_lossy().into_owned());
    }

    // -- includes --------------------------------------------------------

    #[test]
    fn includes_cases() {
        let home = temp_dir("detect-includes");
        let _guard = lock_env(&[("XDG_CONFIG_HOME", home.root().as_str())]);
        let included = write_file(&home.path().join("colours.conf"), GHOSTTY);
        // A commented include is followed — and answers the §7.3 question.
        let main = write_file(
            &home.path().join("config.ghostty"),
            &format!(
                "font-size = 12\nconfig-file = {}  # mine\n",
                included.display()
            ),
        );
        assert_eq!(ghostty_includes(&main), vec![included.clone()]);
        assert!(config_holds_colours::<Fake>("ghostty", &main));

        // Quoted with spaces.
        let spaced = write_file(&home.path().join("my colours.conf"), GHOSTTY);
        let main = write_file(
            &home.path().join("quoted.ghostty"),
            &format!("config-file = \"{}\"\n", spaced.display()),
        );
        assert_eq!(ghostty_includes(&main), vec![spaced]);

        // Bare ?path relative to the config.
        let main = write_file(
            &home.path().join("relative.ghostty"),
            "config-file = ?colours.conf\n",
        );
        assert_eq!(ghostty_includes(&main), vec![included.clone()]);

        // Commented out is ignored; missing is skipped, never invented.
        let main = write_file(
            &home.path().join("commented.ghostty"),
            &format!("# config-file = {}\n", included.display()),
        );
        assert!(ghostty_includes(&main).is_empty());
        let main = write_file(
            &home.path().join("missing.ghostty"),
            "config-file = ?nowhere.conf\n",
        );
        assert!(ghostty_includes(&main).is_empty());
        assert!(!config_holds_colours::<Fake>("ghostty", &main));
    }

    // -- infer_format ----------------------------------------------------

    #[test]
    fn infer_format_cases() {
        let _guard = lock_env(&[]);
        // The holder outlives the reads: dropping it would remove the files.
        let holder = temp_dir("detect-infer");
        // A known name wins over content (kitty syntax in a bare `config`).
        let path = holder.write("config", KITTY);
        assert_eq!(infer_format::<Fake>(&path).as_deref(), Some("ghostty"));
        // Content decides for unknown names (kitty's no-`=` spelling).
        let path = holder.write("theme-x", KITTY);
        assert_eq!(infer_format::<Fake>(&path).as_deref(), Some("kitty"));
        // A tie is not inferable (`key = #…` reads as ghostty AND kitty).
        let path = holder.write("theme-y", GHOSTTY);
        assert_eq!(infer_format::<Fake>(&path), None);
        // An empty file is not inferable.
        let path = holder.write("theme-z", "");
        assert_eq!(infer_format::<Fake>(&path), None);
    }

    // -- theme pointer ---------------------------------------------------

    const CONFIG: &str =
        "# my ghostty\nfont-size = 12\ntheme   = \"ember\"   # mine\n\nwindow-padding-x = 2\n";

    struct PointerHome {
        _guard: crate::testutil::EnvGuard,
        dir: crate::testutil::TestDir,
        config: PathBuf,
    }

    impl PointerHome {
        fn setup() -> Self {
            let dir = temp_dir("detect-pointer");
            let root = dir.root();
            let guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str())]);
            let config = dir.write("ghostty/config.ghostty", CONFIG);
            PointerHome {
                _guard: guard,
                dir,
                config,
            }
        }

        fn write(&self, name: &str, text: &str) -> PathBuf {
            self.dir.write(&format!("ghostty/{name}"), text)
        }

        fn read(&self, path: &Path) -> String {
            self.dir.read(path)
        }
    }

    #[test]
    fn pointer_swap_keeps_everything_else() {
        let home = PointerHome::setup();
        assert_eq!(
            ensure_theme_pointer(&home.config, "dusk").unwrap(),
            PointerAction::Rewritten
        );
        let text = home.read(&home.config);
        let after: Vec<&str> = text.lines().collect();
        let before: Vec<&str> = CONFIG.lines().collect();
        assert_eq!(after.len(), before.len());
        let changed: Vec<usize> = before
            .iter()
            .zip(after.iter())
            .enumerate()
            .filter(|(_, (a, b))| a != b)
            .map(|(i, _)| i)
            .collect();
        assert_eq!(changed, vec![2]);
        assert_eq!(after[2], "theme   = \"dusk\"   # mine");
    }

    #[test]
    fn bare_value_stays_bare() {
        let home = PointerHome::setup();
        home.write("config.ghostty", "theme = ember\n");
        assert_eq!(
            ensure_theme_pointer(&home.config, "dusk").unwrap(),
            PointerAction::Rewritten
        );
        assert_eq!(home.read(&home.config), "theme = dusk\n");
    }

    #[test]
    fn theme_name_reading() {
        let home = PointerHome::setup();
        assert_eq!(ghostty_theme_name(&home.config).as_deref(), Some("ember"));
        let path = home.write("dangling.ghostty", "theme = GhosttyDark\n");
        assert_eq!(ghostty_theme_name(&path).as_deref(), Some("GhosttyDark"));
        let path = home.write("inline.ghostty", "background = #101014\n");
        assert_eq!(ghostty_theme_name(&path), None);
        let path = home.write("commented.ghostty", "# theme = ember\n");
        assert_eq!(ghostty_theme_name(&path), None);
        let path = home.write("empty.ghostty", "theme =\n");
        assert_eq!(ghostty_theme_name(&path), None);
        assert_eq!(
            ghostty_theme_name(&home.dir.path().join("ghostty/missing.ghostty")),
            None
        );
        let path = home.write("comment.ghostty", "theme = ember  # mine\n");
        assert_eq!(ghostty_theme_name(&path).as_deref(), Some("ember"));
        // Last line wins, on the way in and on the way out.
        let path = home.write("twice.ghostty", "theme = first\ntheme = second\n");
        assert_eq!(ghostty_theme_name(&path).as_deref(), Some("second"));
        let path = home.write(
            "mixed.ghostty",
            "theme = first\n# theme = skipped\ntheme = \"second\"\n",
        );
        assert_eq!(ghostty_theme_name(&path).as_deref(), Some("second"));
    }

    #[test]
    fn space_names_followed_and_rewritten() {
        let home = PointerHome::setup();
        home.write("themes/Catppuccin Mocha", "background = #1e1e2e\n");
        home.write("config.ghostty", "theme = Catppuccin Mocha  # dusk\n");
        assert_eq!(
            ghostty_theme_file(&home.config),
            Some(ghostty_themes_dir().join("Catppuccin Mocha"))
        );
        assert_eq!(
            ensure_theme_pointer(&home.config, "dusk").unwrap(),
            PointerAction::Rewritten
        );
        assert_eq!(home.read(&home.config), "theme = dusk  # dusk\n");
        home.write("config.ghostty", "theme = \"Catppuccin Mocha\"  # mine\n");
        assert_eq!(
            ensure_theme_pointer(&home.config, "dusk").unwrap(),
            PointerAction::Rewritten
        );
        assert_eq!(home.read(&home.config), "theme = \"dusk\"  # mine\n");
    }

    #[test]
    fn pointer_append_cases() {
        let home = PointerHome::setup();
        for (text, expected) in [
            ("font-size = 12\n", "font-size = 12\ntheme = dusk\n"),
            ("font-size = 12", "font-size = 12\ntheme = dusk"),
        ] {
            let path = home.write("plain.ghostty", text);
            assert_eq!(
                ensure_theme_pointer(&path, "dusk").unwrap(),
                PointerAction::Appended
            );
            assert_eq!(home.read(&path), expected);
        }
        let path = home.write("empty.ghostty", "");
        assert_eq!(
            ensure_theme_pointer(&path, "dusk").unwrap(),
            PointerAction::Appended
        );
        assert_eq!(home.read(&path), "theme = dusk\n");
        // A commented-out theme line is left alone; the pointer appends.
        let path = home.write("commented.ghostty", "# theme = ghost\nfont-size = 12\n");
        assert_eq!(
            ensure_theme_pointer(&path, "dusk").unwrap(),
            PointerAction::Appended
        );
        assert_eq!(
            home.read(&path),
            "# theme = ghost\nfont-size = 12\ntheme = dusk\n"
        );
        // The last theme line wins, like Ghostty.
        let path = home.write("twice.ghostty", "theme = one\ntheme = two\n");
        ensure_theme_pointer(&path, "dusk").unwrap();
        assert_eq!(home.read(&path), "theme = one\ntheme = dusk\n");
    }

    #[test]
    fn pointer_noop_keeps_mtime() {
        let home = PointerHome::setup();
        let stamp = std::time::SystemTime::now() - std::time::Duration::from_secs(60);
        filetime_set(&home.config, stamp);
        assert_eq!(
            ensure_theme_pointer(&home.config, "ember").unwrap(),
            PointerAction::Unchanged
        );
        assert_eq!(home.read(&home.config), CONFIG);
        assert_eq!(
            std::fs::metadata(&home.config).unwrap().modified().unwrap(),
            stamp
        );
    }

    fn filetime_set(path: &Path, stamp: std::time::SystemTime) {
        // std-only utime: libc is out of scope, so reopen+set via
        // filetimes is unavailable — emulate by setting mtime through
        // `std::fs::File::set_modified` (stable since 1.75).
        let file = std::fs::File::options().write(true).open(path).unwrap();
        file.set_modified(stamp).unwrap();
    }

    #[test]
    fn pointer_crlf_and_refusals() {
        let home = PointerHome::setup();
        let path = home.write("crlf.ghostty", "theme = one\r\nfont-size = 12\r\n");
        assert_eq!(
            ensure_theme_pointer(&path, "dusk").unwrap(),
            PointerAction::Rewritten
        );
        assert_eq!(read_raw(&path), b"theme = dusk\r\nfont-size = 12\r\n");
        let path = home.write("crlf2.ghostty", "font-size = 12\r\n");
        // write() then read_raw: the helper writes UTF-8 text as-is.
        std::fs::write(&path, b"font-size = 12\r\n").unwrap();
        assert_eq!(
            ensure_theme_pointer(&path, "dusk").unwrap(),
            PointerAction::Appended
        );
        assert_eq!(read_raw(&path), b"font-size = 12\r\ntheme = dusk\r\n");

        for bad in ["two lines\ntheme = ghost", "quote\"", "half#hash", ""] {
            assert!(
                ensure_theme_pointer(&home.config, bad).is_err(),
                "accepted {bad:?}"
            );
        }
        assert_eq!(home.read(&home.config), CONFIG);
        assert!(ensure_theme_pointer(&home.dir.path().join("gone.ghostty"), "dusk").is_err());
    }

    #[test]
    fn reader_agrees_with_writer() {
        let home = PointerHome::setup();
        let old = home.write("themes/ember", "background = #101014\n");
        home.write("themes/dusk", "background = #0a0a13\n");
        assert_eq!(ghostty_theme_file(&home.config), Some(old));
        assert!(config_holds_colours::<Fake>("ghostty", &home.config));
        ensure_theme_pointer(&home.config, "dusk").unwrap();
        assert_eq!(
            ghostty_theme_file(&home.config),
            Some(ghostty_themes_dir().join("dusk"))
        );
        assert!(config_holds_colours::<Fake>("ghostty", &home.config));
    }

    #[test]
    fn colourless_configs_are_not_targets() {
        let home = PointerHome::setup();
        let path = home.write("blank.ghostty", "font-size = 12\n");
        assert!(!config_holds_colours::<Fake>("ghostty", &path));
        let path = home.write("dangling.ghostty", "theme = nowhere\n");
        assert!(!config_holds_colours::<Fake>("ghostty", &path));
    }

    #[test]
    fn main_config_is_not_the_theme_file() {
        let home = PointerHome::setup();
        let theme = home.write("themes/ember", "background = #101014\n");
        assert_ne!(ghostty_main_config::<Fake>(None), Some(theme));
        assert_eq!(
            ghostty_main_config::<Fake>(Some(&home.config)),
            Some(home.config.clone())
        );
        assert_eq!(
            ghostty_main_config::<Fake>(Some(&home.dir.path().join("gone.ghostty"))),
            None
        );
    }

    #[test]
    fn shipped_theme_is_copied_for_editing() {
        let home = temp_dir("detect-shipped");
        let root = home.root();
        let _guard = lock_env(&[
            ("XDG_CONFIG_HOME", root.as_str()),
            (
                "GHOSTTY_RESOURCES_DIR",
                home.path().join("resources").to_str().unwrap(),
            ),
        ]);
        let shipped = home.write("resources/themes/Shipped", "background = #101014\n");
        let config = home.write("ghostty/config", "theme = Shipped\n");
        let found = ghostty_theme_file(&config).unwrap();
        assert_eq!(found, ghostty_themes_dir().join("Shipped"));
        assert_eq!(
            std::fs::read_to_string(&found).unwrap(),
            std::fs::read_to_string(&shipped).unwrap()
        );
    }

    // -- resolve ---------------------------------------------------------

    #[test]
    fn resolve_cases() {
        let _guard = lock_env(&[]);
        let (_, _, error) = resolve::<Fake>(Some("wezterm"), None);
        assert_eq!(error.as_deref(), Some("unknown format 'wezterm'"));
        let (fmt, target, error) =
            resolve::<Fake>(Some("ghostty"), Some(Path::new("/nonexistent/huebox-x")));
        assert_eq!(fmt.as_deref(), Some("ghostty"));
        assert_eq!(target, None);
        assert!(error.unwrap().starts_with("no such file: "));
        // No candidates anywhere: the honest failure, naming the registry.
        let (_, _, error) = resolve::<Fake>(None, None);
        assert!(error
            .unwrap()
            .contains("found no terminal config with colours"));
        let (_, _, error) = resolve::<Fake>(Some("alacritty"), None);
        assert_eq!(
            error.as_deref(),
            Some("no alacritty config with colours found")
        );
        // An explicit path that says neither format resolves colourless.
        let home = temp_dir("detect-resolve");
        let blank = home.write("mystery", "font-size = 12\n");
        let (fmt, target, error) = resolve::<Fake>(None, Some(&blank));
        assert_eq!(fmt, None);
        assert_eq!(target, Some(blank));
        assert_eq!(error, None);
    }
}
