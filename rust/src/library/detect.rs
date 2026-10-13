//! Finding the terminal you are in and the config holding its colours.
//!
//! Rust port of `huebox/detect.py`. Finding a config is only half of it:
//! [`ensure_theme_pointer`] writes the one line deciding *which* file a
//! Ghostty config reads, with the same line-level discipline every other
//! write to somebody else's file follows. [`resolve`] is the facade for
//! finding the push target.

use std::path::{Path, PathBuf};

use crate::library::format::FormatStub;

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

/// Expand one defaults-table entry at probe time: a literal
/// `$XDG_CONFIG_DIRS/...` entry (the kitty row, decision 48) becomes one
/// candidate per colon-separated dir, empties skipped. A leading marker
/// only — anything else passes through untouched.
///
/// Unset or empty falls back to `/etc/xdg`, per the XDG basedir spec
/// ("not set or empty" means `/etc/xdg`); there is deliberately no
/// other fallback, so the table owns system defaults explicitly.
fn expand_config_dirs(entry: &str) -> Vec<String> {
    const MARKER: &str = "$XDG_CONFIG_DIRS";
    let rest = match entry.strip_prefix(MARKER) {
        Some(rest) => rest,
        None => return vec![entry.to_owned()],
    };
    let dirs = std::env::var("XDG_CONFIG_DIRS").unwrap_or_default();
    let parts: Vec<&str> = dirs.split(':').filter(|dir| !dir.is_empty()).collect();
    let fallback = ["/etc/xdg"];
    let dirs: &[&str] = if parts.is_empty() { &fallback } else { &parts };
    dirs.iter().map(|dir| format!("{dir}{rest}")).collect()
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
    // `$XDG_CONFIG_DIRS` literals expand here, in place, so the expanded
    // stops keep the table's probe order (decision 48).
    let expanded: Vec<String> = F::default_paths(fmt)
        .iter()
        .flat_map(|entry| expand_config_dirs(entry))
        .collect();
    let mut defaults = prefer_xdg(&expanded, &home);
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

/// The directory `path`'s own include lines resolve against: its parent,
/// absolutised so nested files compare equal in the visited set.
fn holding_dir(config_path: &Path) -> PathBuf {
    config_path
        .parent()
        .map(|parent| std::path::absolute(parent).unwrap_or_else(|_| parent.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Follow `direct` includes transitively, deepest first per branch, with a
/// visited set: a revisited file stops its chain silently, never an error
/// (decision 56). `start` itself seeds the set, so a self-include ends.
/// Comparison is path-lexical (`absolute`, no symlink resolution).
fn follow_chain(start: &Path, direct: fn(&Path) -> Vec<PathBuf>) -> Vec<PathBuf> {
    fn absolute(path: &Path) -> PathBuf {
        std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf())
    }
    fn walk(
        path: &Path,
        direct: fn(&Path) -> Vec<PathBuf>,
        visited: &mut std::collections::HashSet<PathBuf>,
        out: &mut Vec<PathBuf>,
    ) {
        for next in direct(path) {
            if !visited.insert(absolute(&next)) {
                continue;
            }
            out.push(next.clone());
            walk(&next, direct, visited, out);
        }
    }
    let mut visited = std::collections::HashSet::new();
    visited.insert(absolute(start));
    let mut out = Vec::new();
    walk(start, direct, &mut visited, &mut out);
    out
}

/// Config files pulled in via Ghostty `config-file` lines, followed
/// recursively: a pulled-in file's own `config-file` lines resolve against
/// the file holding them, and a revisited file stops its chain silently
/// (decision 56). In include order, depth-first per branch.
pub fn ghostty_includes(config_path: &Path) -> Vec<PathBuf> {
    follow_chain(config_path, ghostty_direct_includes)
}

/// One Ghostty `config-file` line's direct (single-level) step.
///
/// A trailing comment is a comment. A leading `?` marks an optional
/// include — stripped, and a missing file skipped, never an error; any
/// other `?` is a literal filename char. A relative path resolves against
/// the file holding the line, not the top config.
fn ghostty_direct_includes(config_path: &Path) -> Vec<PathBuf> {
    let bytes = match std::fs::read(config_path) {
        Ok(bytes) => bytes,
        Err(_) => return Vec::new(),
    };
    let directory = holding_dir(config_path);
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
        let value = raw.trim();
        // A leading `?` marks an optional include; any other `?` is a
        // literal filename char.
        let relative = value.strip_prefix('?').unwrap_or(value);
        if relative.is_empty() {
            continue;
        }
        let expanded = expanduser(relative);
        let target = if Path::new(&expanded).is_absolute() {
            PathBuf::from(expanded)
        } else {
            directory.join(&expanded)
        };
        // Missing is skipped, never an error — optional or not, detection
        // must never fail on a dangling include.
        if target.is_file() {
            out.push(target);
        }
    }
    out
}

/// Config files pulled in via kitty `include` / `globinclude` lines,
/// followed recursively like [`ghostty_includes`] (decision 56).
/// Detection only (decision 51): the push target stays the resolved file.
///
/// kitty is space-separated `key value`, so `#` starts a comment only at
/// a line's start — a colour value may itself begin with `#`. A relative
/// path resolves against the file holding the line; a missing file is
/// skipped, never an error.
pub fn kitty_includes(config_path: &Path) -> Vec<PathBuf> {
    follow_chain(config_path, kitty_direct_includes)
}

/// One kitty `include` / `globinclude` line's direct step.
fn kitty_direct_includes(config_path: &Path) -> Vec<PathBuf> {
    let bytes = match std::fs::read(config_path) {
        Ok(bytes) => bytes,
        Err(_) => return Vec::new(),
    };
    let directory = holding_dir(config_path);
    let mut out = Vec::new();
    for line in String::from_utf8_lossy(&bytes).lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut halves = line.splitn(2, char::is_whitespace);
        let key = halves.next().unwrap_or("");
        if key != "include" && key != "globinclude" {
            continue;
        }
        let mut value = halves.next().unwrap_or("").trim().to_owned();
        if value.len() >= 2
            && ((value.starts_with('"') && value.ends_with('"'))
                || (value.starts_with('\'') && value.ends_with('\'')))
        {
            value.truncate(value.len() - 1);
            value.remove(0);
        }
        let value = value.trim();
        if value.is_empty() {
            continue;
        }
        let expanded = expanduser(value);
        let target = if Path::new(&expanded).is_absolute() {
            PathBuf::from(expanded)
        } else {
            directory.join(&expanded)
        };
        if key == "globinclude" {
            out.extend(expand_glob_path(&target));
        } else if target.is_file() {
            out.push(target);
        }
    }
    out
}

/// Does `pattern`'s file name carry glob metacharacters?
fn has_glob_meta(text: &str) -> bool {
    text.bytes().any(|c| matches!(c, b'*' | b'?' | b'['))
}

/// Match a file name against `*`, `?` and `[...]` (ranges, `!`/`^`
/// negation, a first-position `]` literal; an unterminated class is a
/// literal `[`). Byte-wise: `?` spans one byte, not one char.
fn glob_match(pattern: &str, name: &str) -> bool {
    fn rec(pattern: &[u8], name: &[u8]) -> bool {
        if pattern.is_empty() {
            return name.is_empty();
        }
        match pattern[0] {
            b'*' => {
                let mut rest = &pattern[1..];
                while rest.first() == Some(&b'*') {
                    rest = &rest[1..];
                }
                (0..=name.len()).any(|i| rec(rest, &name[i..]))
            }
            b'?' => !name.is_empty() && rec(&pattern[1..], &name[1..]),
            b'[' => {
                if name.is_empty() {
                    return false;
                }
                let mut i = 1;
                let mut negated = false;
                if matches!(pattern.get(i), Some(b'!') | Some(b'^')) {
                    negated = true;
                    i += 1;
                }
                // A `]` in first position is a literal member.
                let mut end = i;
                if pattern.get(end) == Some(&b']') {
                    end += 1;
                }
                while end < pattern.len() && pattern[end] != b']' {
                    end += 1;
                }
                if end >= pattern.len() {
                    return name[0] == b'[' && rec(&pattern[1..], &name[1..]);
                }
                let mut matched = false;
                let mut k = i;
                if k < end && pattern[k] == b']' {
                    matched |= name[0] == b']';
                    k += 1;
                }
                while k < end {
                    if k + 2 < end && pattern[k + 1] == b'-' {
                        matched |= name[0] >= pattern[k] && name[0] <= pattern[k + 2];
                        k += 3;
                    } else {
                        matched |= name[0] == pattern[k];
                        k += 1;
                    }
                }
                matched != negated && rec(&pattern[end + 1..], &name[1..])
            }
            literal => !name.is_empty() && name[0] == literal && rec(&pattern[1..], &name[1..]),
        }
    }
    rec(pattern.as_bytes(), name.as_bytes())
}

/// Expand a `globinclude` target: only the file name may glob (a
/// metacharacter in the directory part is literal); hits are files only,
/// sorted. No match — or an unreadable directory — is empty, never an error.
fn expand_glob_path(pattern: &Path) -> Vec<PathBuf> {
    if pattern.is_file() {
        return vec![pattern.to_path_buf()];
    }
    let text = pattern.to_string_lossy();
    let (dir, file) = match text.rfind('/') {
        Some(0) => ("/".to_owned(), text[1..].to_owned()),
        Some(i) => (text[..i].to_owned(), text[i + 1..].to_owned()),
        None => (".".to_owned(), text.into_owned()),
    };
    if !has_glob_meta(&file) || has_glob_meta(&dir) {
        return Vec::new();
    }
    let entries = match std::fs::read_dir(&dir) {
        Ok(entries) => entries,
        Err(_) => return Vec::new(),
    };
    let mut hits: Vec<PathBuf> = entries
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| path.is_file())
        .filter(|path| {
            path.file_name()
                .is_some_and(|base| glob_match(&file, &base.to_string_lossy()))
        })
        .collect();
    hits.sort();
    hits
}

/// Config files pulled in via Alacritty `general.import`, followed
/// recursively like [`ghostty_includes`] (decision 56). Detection only
/// (decision 51): the push target stays the resolved file.
///
/// A minimal TOML scan, not a parser: the `import` key under `[general]`,
/// or a top-level `general.import` dotted key, holding an array of quoted
/// strings (single-line or multi-line). Anything else TOML can express —
/// quoted table names, sub-tables carrying their own `import` — is out of
/// scope and ignored. Relative paths resolve against the file holding the
/// entry; missing files are skipped, never an error.
pub fn alacritty_imports(config_path: &Path) -> Vec<PathBuf> {
    follow_chain(config_path, alacritty_direct_imports)
}

/// One Alacritty config's direct `general.import` step.
fn alacritty_direct_imports(config_path: &Path) -> Vec<PathBuf> {
    let bytes = match std::fs::read(config_path) {
        Ok(bytes) => bytes,
        Err(_) => return Vec::new(),
    };
    let directory = holding_dir(config_path);
    let mut out = Vec::new();
    let mut section = String::new();
    let mut pending: Option<String> = None;
    for raw in String::from_utf8_lossy(&bytes).lines() {
        // Inside a multi-line array every line accumulates, even one that
        // looks like a section header (valid TOML cannot open one there).
        if let Some(accumulated) = pending.take() {
            let accumulated = format!("{accumulated}\n{}", strip_toml_comment(raw).trim());
            if toml_bracket_depth(&accumulated) <= 0 {
                out.extend(toml_string_array(&accumulated, &directory));
            } else {
                pending = Some(accumulated);
            }
            continue;
        }
        let line = strip_toml_comment(raw);
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(name) = toml_section_header(trimmed) {
            section = name;
            continue;
        }
        let (key, value) = match toml_key_value(trimmed) {
            Some(pair) => pair,
            None => continue,
        };
        let bare: String = key.chars().filter(|c| !c.is_whitespace()).collect();
        let is_import = (section == "general" && bare == "import")
            || (section.is_empty() && bare == "general.import");
        if !is_import {
            continue;
        }
        let value = value.trim();
        if !value.starts_with('[') {
            continue;
        }
        if toml_bracket_depth(value) <= 0 {
            out.extend(toml_string_array(value, &directory));
        } else {
            pending = Some(value.to_owned());
        }
    }
    out
}

/// Strip a TOML `#` comment: a `#` outside single/double quotes ends the
/// line; inside either it is data.
fn strip_toml_comment(line: &str) -> &str {
    let bytes = line.as_bytes();
    let mut quote: Option<u8> = None;
    let mut i = 0;
    while i < bytes.len() {
        match quote {
            Some(q) => {
                if bytes[i] == b'\\' && q == b'"' && i + 1 < bytes.len() {
                    i += 2;
                } else if bytes[i] == q {
                    quote = None;
                    i += 1;
                } else {
                    i += 1;
                }
            }
            None => match bytes[i] {
                b'"' | b'\'' => {
                    quote = Some(bytes[i]);
                    i += 1;
                }
                b'#' => return &line[..i],
                _ => i += 1,
            },
        }
    }
    line
}

/// A bare `[table]` header's name, trimmed; `[[array]]` and quoted names
/// are out of scope and answer `None`.
fn toml_section_header(trimmed: &str) -> Option<String> {
    let inner = trimmed.strip_prefix('[')?.strip_suffix(']')?.trim();
    if inner.is_empty() || inner.starts_with('[') || inner.contains(['"', '\'']) {
        return None;
    }
    Some(inner.to_owned())
}

/// Split `key = value` at the first `=` outside quotes.
fn toml_key_value(trimmed: &str) -> Option<(&str, &str)> {
    let bytes = trimmed.as_bytes();
    let mut quote: Option<u8> = None;
    let mut i = 0;
    while i < bytes.len() {
        match quote {
            Some(q) => {
                if bytes[i] == b'\\' && q == b'"' && i + 1 < bytes.len() {
                    i += 2;
                } else {
                    if bytes[i] == q {
                        quote = None;
                    }
                    i += 1;
                }
            }
            None => match bytes[i] {
                b'"' | b'\'' => {
                    quote = Some(bytes[i]);
                    i += 1;
                }
                b'=' => return Some((trimmed[..i].trim(), trimmed[i + 1..].trim())),
                _ => i += 1,
            },
        }
    }
    None
}

/// Bracket depth of `[` over `]`, quote-aware so a `[` inside a string
/// does not open a level. Used to spot a multi-line array's end.
fn toml_bracket_depth(text: &str) -> i32 {
    let bytes = text.as_bytes();
    let mut quote: Option<u8> = None;
    let mut depth = 0;
    let mut i = 0;
    while i < bytes.len() {
        match quote {
            Some(q) => {
                if bytes[i] == b'\\' && q == b'"' && i + 1 < bytes.len() {
                    i += 2;
                } else {
                    if bytes[i] == q {
                        quote = None;
                    }
                    i += 1;
                }
            }
            None => {
                match bytes[i] {
                    b'"' | b'\'' => quote = Some(bytes[i]),
                    b'[' => depth += 1,
                    b']' => depth -= 1,
                    _ => {}
                }
                i += 1;
            }
        }
    }
    depth
}

/// Every quoted string in a `[...]` array, resolved like any other
/// include: `~`-expanded, absolute as-is, relative against the holding
/// file's directory, missing skipped. Only double/single-quoted strings
/// count — bare words are not valid TOML there.
fn toml_string_array(array: &str, directory: &Path) -> Vec<PathBuf> {
    let bytes = array.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        let quote = bytes[i];
        if quote != b'"' && quote != b'\'' {
            i += 1;
            continue;
        }
        let mut value = Vec::new();
        i += 1;
        while i < bytes.len() && bytes[i] != quote {
            if bytes[i] == b'\\' && quote == b'"' && i + 1 < bytes.len() {
                i += 1;
                value.push(bytes[i]);
                i += 1;
            } else {
                value.push(bytes[i]);
                i += 1;
            }
        }
        i += 1; // past the closing quote (or off the end)
        let value = String::from_utf8_lossy(&value);
        let value = value.trim();
        if value.is_empty() {
            continue;
        }
        let expanded = expanduser(value);
        let target = if Path::new(&expanded).is_absolute() {
            PathBuf::from(expanded)
        } else {
            directory.join(&expanded)
        };
        if target.is_file() {
            out.push(target);
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

/// Where Ghostty's shipped themes live: `$GHOSTTY_RESOURCES_DIR` when set
/// (empty counts as unset), else `share/ghostty` on Linux and the app
/// bundle's `Contents/Resources/ghostty` on macOS (cfg-gated). Read at
/// call time so tests can override it.
pub fn ghostty_resources_dir() -> PathBuf {
    match std::env::var("GHOSTTY_RESOURCES_DIR") {
        Ok(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => {
            #[cfg(target_os = "macos")]
            {
                PathBuf::from("/Applications/Ghostty.app/Contents/Resources/ghostty")
            }
            #[cfg(not(target_os = "macos"))]
            {
                PathBuf::from("/usr/share/ghostty")
            }
        }
    }
}

/// Every name the effective `theme =` line carries, in order (decision 55).
///
/// Ghostty reads the last `theme =` line; a `light:Name,dark:Name` pair
/// carries two names, light first, and a plain value carries one. A
/// missing/unreadable config — or one with no usable value — carries none.
pub fn ghostty_theme_names(config_path: &Path) -> Vec<String> {
    let bytes = match std::fs::read(config_path) {
        Ok(bytes) => bytes,
        Err(_) => return Vec::new(),
    };
    let mut raw: Option<String> = None;
    for line in bytes.split(|b| *b == b'\n') {
        if is_comment_line(line) {
            continue;
        }
        if let Some(hit) = match_assignment(line, b"theme") {
            raw = Some(String::from_utf8_lossy(&line[hit.value_start..hit.value_end]).into_owned());
        }
    }
    split_theme_value(&raw.unwrap_or_default())
}

/// Split one raw `theme =` value into the names it carries: a
/// `light:`/`dark:` pair splits on commas (each part's prefix stripped,
/// empties dropped); anything else is one name, commas and all — the pair
/// form is recognised by its prefixes, never guessed from a comma. The
/// pair is never a file name: resolution tries each name in turn.
fn split_theme_value(raw: &str) -> Vec<String> {
    let is_pair = raw.split(',').any(|part| {
        let part = part.trim();
        part.starts_with("light:") || part.starts_with("dark:")
    });
    if !is_pair {
        return if raw.is_empty() {
            Vec::new()
        } else {
            vec![raw.to_owned()]
        };
    }
    raw.split(',')
        .map(|part| {
            let part = part.trim();
            part.strip_prefix("light:")
                .or_else(|| part.strip_prefix("dark:"))
                .unwrap_or(part)
                .trim()
                .to_owned()
        })
        .filter(|name| !name.is_empty())
        .collect()
}

/// The theme `config_path`'s last `theme =` line names, or `None`.
///
/// The name a config is *on*, with no opinion about whether the file
/// behind it exists. A missing/unreadable config is on no theme.
/// Delegates to the first of [`ghostty_theme_names`].
pub fn ghostty_theme_name(config_path: &Path) -> Option<String> {
    ghostty_theme_names(config_path).into_iter().next()
}

/// Follow `theme =` to the file holding the colours (decision 50).
///
/// Every name [`ghostty_theme_names`] carries is tried in order — a
/// `light:`/`dark:` pair resolves both, first hit wins. A bare name looks
/// under `$XDG_CONFIG_HOME/ghostty/themes` first, then the resources
/// `themes/` dir; an absolute path is used as-is (no copy, no second
/// guess — a missing one simply misses).
///
/// Upstream does not search `~/Library/Application Support/.../themes`
/// for named themes (ghostty-org/ghostty#12886), so neither does huebox:
/// only the user dir and the resources dir are consulted.
///
/// A read-only shipped theme is copied where we may edit it, as in
/// `detect.py`. A copy failure skips to the next name (defensive:
/// `resolve` must never fail a save's target search on a permissions
/// edge — the push reports colourless instead).
pub fn ghostty_theme_file(config_path: &Path) -> Option<PathBuf> {
    for name in ghostty_theme_names(config_path) {
        if name.is_empty() {
            continue;
        }
        if Path::new(&name).is_absolute() {
            let path = PathBuf::from(&name);
            if path.is_file() {
                return Some(path);
            }
            continue;
        }
        let user = ghostty_themes_dir().join(&name);
        if user.is_file() {
            return Some(user);
        }
        let shipped = ghostty_resources_dir().join("themes").join(&name);
        if shipped.is_file() {
            if let Some(parent) = user.parent() {
                if std::fs::create_dir_all(parent).is_err() {
                    continue;
                }
            }
            let mode = std::fs::metadata(&shipped)
                .ok()
                .map(|meta| meta.permissions());
            if std::fs::copy(&shipped, &user).is_err() {
                continue;
            }
            if let Some(mode) = mode {
                let _ = std::fs::set_permissions(&user, mode);
            }
            return Some(user);
        }
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
/// theme file, and still be a real terminal config. kitty `include` /
/// `globinclude` and Alacritty `general.import` count the same way, for
/// detection only (decision 51).
pub fn config_holds_colours<F: FormatStub>(fmt: &str, path: &Path) -> bool {
    if !F::read(fmt, path).is_empty() {
        return true;
    }
    // What one file pulls in, per format; includes nest through
    // `*_includes`' own visited set.
    let chained: Vec<PathBuf> = match fmt {
        "ghostty" => ghostty_includes(path),
        "kitty" => kitty_includes(path),
        "alacritty" => alacritty_imports(path),
        _ => Vec::new(),
    };
    for included in chained {
        if !F::read(fmt, &included).is_empty() {
            return true;
        }
    }
    if fmt == "ghostty" {
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

/// A path huebox must never push to: anything under `/etc` (decisions
/// 48/49/54). Component-wise, so `/etcfoo` is not system but `/etc`
/// itself is.
fn is_system_path(path: &Path) -> bool {
    path.starts_with("/etc")
}

/// First config for `fmt` that actually defines colours. In push mode
/// every `/etc` stop is skipped — candidate, include, or theme file — so
/// a push can never land on a root-owned file (decision 54).
fn resolve_for<F: FormatStub>(fmt: &str, push_mode: bool) -> Option<PathBuf> {
    for candidate in candidate_paths::<F>(fmt) {
        if push_mode && is_system_path(&candidate) {
            continue;
        }
        let mut target = candidate;
        // Includes answer before the file itself, as Ghostty's already
        // did: a split config's colours live where they live.
        let chained: Vec<PathBuf> = match fmt {
            "ghostty" => ghostty_includes(&target),
            "kitty" => kitty_includes(&target),
            "alacritty" => alacritty_imports(&target),
            _ => Vec::new(),
        };
        for included in chained {
            if push_mode && is_system_path(&included) {
                continue;
            }
            if !F::read(fmt, &included).is_empty() {
                return Some(included);
            }
        }
        if fmt == "ghostty" {
            if let Some(themed) = ghostty_theme_file(&target) {
                target = themed;
            }
        }
        if push_mode && is_system_path(&target) {
            continue;
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
///
/// Detection keeps system paths: reading `/etc` stops is how huebox sees
/// a system-wide setup. Push callers want [`resolve_for_push`] instead.
pub fn resolve<F: FormatStub>(
    fmt: Option<&str>,
    path: Option<&Path>,
) -> (Option<String>, Option<PathBuf>, Option<String>) {
    resolve_inner::<F>(fmt, path, false)
}

/// Push-mode resolution (decision 54): like [`resolve`], but paths under
/// `/etc` are excluded — an auto-detected system stop is skipped, and an
/// explicit `--config` under `/etc` is refused outright, so every caller
/// routing its push through here can never write a root-owned file.
/// Detection (preview, import, `--dump`) keeps calling [`resolve`],
/// system paths included.
pub fn resolve_for_push<F: FormatStub>(
    fmt: Option<&str>,
    path: Option<&Path>,
) -> (Option<String>, Option<PathBuf>, Option<String>) {
    if let Some(explicit) = path {
        let expanded = expanduser(&explicit.to_string_lossy());
        if is_system_path(Path::new(&expanded)) {
            return (
                fmt.map(str::to_owned),
                None,
                Some(format!("refusing to push to system path: {expanded}")),
            );
        }
    }
    resolve_inner::<F>(fmt, path, true)
}

/// Shared body behind [`resolve`] (detection) and [`resolve_for_push`]
/// (push): `push_mode` filters `/etc` stops out of the candidates.
fn resolve_inner<F: FormatStub>(
    fmt: Option<&str>,
    path: Option<&Path>,
    push_mode: bool,
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
        if let Some(found) = resolve_for::<F>(&name, push_mode) {
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
    use crate::library::providers::tests::Fake;
    use crate::library::testutil::{lock_env, temp_dir};

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
        _guard: crate::library::testutil::EnvGuard,
        dir: crate::library::testutil::TestDir,
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

    // -- decision 48: $XDG_CONFIG_DIRS ---------------------------------

    /// Stand-in for worker A's kitty row: the real `$XDG_CONFIG_DIRS`
    /// literal lives in the format tables (worker A owns those) — this
    /// stub only proves probe-time expansion in `candidate_paths`.
    struct XdgDirs;

    impl FormatStub for XdgDirs {
        fn format_names() -> &'static [&'static str] {
            &["kitty"]
        }
        fn default_paths(fmt: &str) -> Vec<String> {
            match fmt {
                "kitty" => vec!["$XDG_CONFIG_DIRS/kitty/kitty.conf".to_owned()],
                _ => vec![],
            }
        }
        fn env_table(_fmt: &str) -> &'static [(&'static str, &'static str)] {
            &[]
        }
        fn read(_fmt: &str, _path: &Path) -> crate::library::SlotMap {
            crate::library::SlotMap::new()
        }
        fn write(
            _fmt: &str,
            _path: &Path,
            _slots: &crate::library::SlotMap,
        ) -> std::io::Result<()> {
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "test stub has no writer",
            ))
        }
        fn color_line(_fmt: &str, _line: &str) -> bool {
            false
        }
    }

    /// `XDG_CONFIG_DIRS` is outside testutil's managed set, so tests
    /// driving it save/set/restore manually while holding the env lock.
    struct XdgDirsGuard {
        saved: Option<String>,
    }

    impl XdgDirsGuard {
        fn set(value: &str) -> Self {
            let saved = std::env::var("XDG_CONFIG_DIRS").ok();
            std::env::set_var("XDG_CONFIG_DIRS", value);
            XdgDirsGuard { saved }
        }

        fn unset() -> Self {
            let saved = std::env::var("XDG_CONFIG_DIRS").ok();
            std::env::remove_var("XDG_CONFIG_DIRS");
            XdgDirsGuard { saved }
        }
    }

    impl Drop for XdgDirsGuard {
        fn drop(&mut self) {
            match self.saved.take() {
                Some(value) => std::env::set_var("XDG_CONFIG_DIRS", value),
                None => std::env::remove_var("XDG_CONFIG_DIRS"),
            }
        }
    }

    #[test]
    fn xdg_config_dirs_expand_at_probe_time() {
        let _guard = lock_env(&[]);
        let home = temp_dir("detect-xdg-dirs");
        let first = home.path().join("first");
        let second = home.path().join("second");
        write_file(&first.join("kitty/kitty.conf"), KITTY);
        write_file(&second.join("kitty/kitty.conf"), KITTY);
        // Empties skipped, order kept.
        let _dirs = XdgDirsGuard::set(&format!("{}::{}:", first.display(), second.display()));
        assert_eq!(
            candidate_paths::<XdgDirs>("kitty"),
            vec![
                first.join("kitty/kitty.conf"),
                second.join("kitty/kitty.conf")
            ]
        );
        drop(_dirs);
        // A non-marker entry passes through untouched.
        let _dirs = XdgDirsGuard::set("/nowhere-xdg-dirs");
        assert_eq!(
            expand_config_dirs("~/.kitty.conf"),
            vec!["~/.kitty.conf".to_owned()]
        );
        drop(_dirs);
        // Unset or empty means the XDG fallback, per the basedir spec.
        let _dirs = XdgDirsGuard::unset();
        assert_eq!(
            expand_config_dirs("$XDG_CONFIG_DIRS/kitty/kitty.conf"),
            vec!["/etc/xdg/kitty/kitty.conf".to_owned()]
        );
        drop(_dirs);
        let _dirs = XdgDirsGuard::set("");
        assert_eq!(
            expand_config_dirs("$XDG_CONFIG_DIRS/kitty/kitty.conf"),
            vec!["/etc/xdg/kitty/kitty.conf".to_owned()]
        );
    }

    // -- decisions 55/50: theme names and resolution ---------------------

    #[test]
    fn theme_names_split_pairs_in_order() {
        // PointerHome::setup holds the env lock itself: no outer guard here.
        let home = PointerHome::setup();
        // Plain values carry one name; `theme_name` is its first.
        let path = home.write("plain.ghostty", "theme = ember\n");
        assert_eq!(ghostty_theme_names(&path), vec!["ember".to_owned()]);
        assert_eq!(ghostty_theme_name(&path).as_deref(), Some("ember"));
        // A pair carries two, light first.
        let path = home.write("pair.ghostty", "theme = light:One,dark:Two\n");
        assert_eq!(
            ghostty_theme_names(&path),
            vec!["One".to_owned(), "Two".to_owned()]
        );
        assert_eq!(ghostty_theme_name(&path).as_deref(), Some("One"));
        // Spacing around the pair is not significant.
        let path = home.write("spaced.ghostty", "theme = light: One , dark: Two \n");
        assert_eq!(
            ghostty_theme_names(&path),
            vec!["One".to_owned(), "Two".to_owned()]
        );
        // The last line still wins, pairs included.
        let path = home.write("twice.ghostty", "theme = First\ntheme = light:A,dark:B\n");
        assert_eq!(
            ghostty_theme_names(&path),
            vec!["A".to_owned(), "B".to_owned()]
        );
        // A comma without prefixes is one name, not a pair.
        let path = home.write("comma.ghostty", "theme = a,b\n");
        assert_eq!(ghostty_theme_names(&path), vec!["a,b".to_owned()]);
        // Nothing usable is no names.
        let path = home.write("none.ghostty", "background = #101014\n");
        assert!(ghostty_theme_names(&path).is_empty());
        assert_eq!(ghostty_theme_name(&path), None);
        assert!(ghostty_theme_names(&home.dir.path().join("gone.ghostty")).is_empty());
    }

    #[test]
    fn theme_file_uses_absolute_paths_as_is() {
        let home = PointerHome::setup();
        let theme = home.write("elsewhere/custom", "background = #101014\n");
        let path = home.write("abs.ghostty", &format!("theme = {}\n", theme.display()));
        // Used as-is: returned directly, never copied into the themes dir.
        assert_eq!(ghostty_theme_file(&path), Some(theme));
        assert!(!ghostty_themes_dir().join("custom").exists());
        // A missing absolute path simply misses.
        let path = home.write("abs-gone.ghostty", "theme = /nonexistent/huebox-x\n");
        assert_eq!(ghostty_theme_file(&path), None);
    }

    #[test]
    fn theme_file_pair_first_hit_wins() {
        let home = PointerHome::setup();
        home.write("themes/Two", "background = #101014\n");
        let path = home.write("pair.ghostty", "theme = light:One,dark:Two\n");
        assert_eq!(
            ghostty_theme_file(&path),
            Some(ghostty_themes_dir().join("Two"))
        );
        home.write("themes/One", "background = #0a0a13\n");
        assert_eq!(
            ghostty_theme_file(&path),
            Some(ghostty_themes_dir().join("One"))
        );
        // A missing absolute first name falls through to the second.
        let path = home.write(
            "mixed.ghostty",
            "theme = light:/nonexistent/huebox-x,dark:Two\n",
        );
        assert_eq!(
            ghostty_theme_file(&path),
            Some(ghostty_themes_dir().join("Two"))
        );
    }

    #[test]
    fn resources_dir_override_and_default() {
        // Empty counts as unset: the platform default answers.
        let _guard = lock_env(&[("GHOSTTY_RESOURCES_DIR", "")]);
        #[cfg(not(target_os = "macos"))]
        assert_eq!(ghostty_resources_dir(), PathBuf::from("/usr/share/ghostty"));
        #[cfg(target_os = "macos")]
        assert_eq!(
            ghostty_resources_dir(),
            PathBuf::from("/Applications/Ghostty.app/Contents/Resources/ghostty")
        );
        // `GHOSTTY_RESOURCES_DIR` is managed, so `lock_env` restores it;
        // the shipped-theme test above already proves the override path.
    }

    // -- decision 56: ghostty nesting and cycles --------------------------

    #[test]
    fn ghostty_includes_nest_and_stop_cycles() {
        let home = temp_dir("detect-nested");
        let _guard = lock_env(&[("XDG_CONFIG_HOME", home.root().as_str())]);
        // A chain resolves each line against the file holding it.
        let leaf = write_file(&home.path().join("sub/leaf.conf"), GHOSTTY);
        let mid = write_file(
            &home.path().join("sub/mid.conf"),
            "config-file = ?leaf.conf\n",
        );
        let main = write_file(
            &home.path().join("main.ghostty"),
            "config-file = sub/mid.conf\n",
        );
        assert_eq!(ghostty_includes(&main), vec![mid.clone(), leaf.clone()]);
        assert!(config_holds_colours::<Fake>("ghostty", &main));
        // A cycle stops silently, never an error.
        let a = home.path().join("a.ghostty");
        let b = home.path().join("b.ghostty");
        write_file(&a, "config-file = b.ghostty\n");
        write_file(&b, "config-file = a.ghostty\n");
        assert_eq!(ghostty_includes(&a), vec![b.clone()]);
        assert_eq!(ghostty_includes(&b), vec![a.clone()]);
        assert!(!config_holds_colours::<Fake>("ghostty", &a));
        // A self-include ends at once.
        let selfie = write_file(
            &home.path().join("self.ghostty"),
            "config-file = ?self.ghostty\n",
        );
        assert!(ghostty_includes(&selfie).is_empty());
    }

    #[test]
    fn ghostty_optional_mark_is_leading_only() {
        let home = temp_dir("detect-optional");
        let _guard = lock_env(&[("XDG_CONFIG_HOME", home.root().as_str())]);
        // Leading `?`: optional — existing is followed, missing skipped.
        let present = write_file(&home.path().join("present.conf"), GHOSTTY);
        let main = write_file(
            &home.path().join("main.ghostty"),
            "config-file = ?present.conf\nconfig-file = ?gone.conf\n",
        );
        assert_eq!(ghostty_includes(&main), vec![present]);
        // Any other `?` is a literal filename char.
        let weird = write_file(&home.path().join("we?ird.conf"), GHOSTTY);
        let main = write_file(
            &home.path().join("weird.ghostty"),
            "config-file = we?ird.conf\n",
        );
        assert_eq!(ghostty_includes(&main), vec![weird]);
    }

    // -- decision 51: kitty includes --------------------------------------

    #[test]
    fn kitty_includes_cases() {
        let home = temp_dir("detect-kitty-includes");
        let _guard = lock_env(&[("XDG_CONFIG_HOME", home.root().as_str())]);
        let colours = write_file(&home.path().join("colours.conf"), KITTY);
        // A plain `include` is followed for detection.
        let main = write_file(
            &home.path().join("kitty.conf"),
            "font_size 12\ninclude colours.conf\n",
        );
        assert_eq!(kitty_includes(&main), vec![colours.clone()]);
        assert!(config_holds_colours::<Fake>("kitty", &main));
        // Absolute paths work; missing files are skipped, never an error.
        let main = write_file(
            &home.path().join("abs.conf"),
            &format!(
                "include {}\ninclude /nonexistent/huebox-x\n",
                colours.display()
            ),
        );
        assert_eq!(kitty_includes(&main), vec![colours.clone()]);
        // Full-line comments are ignored — and a colour line is never an
        // include even though its value starts with `#`.
        let main = write_file(
            &home.path().join("commented.conf"),
            "# include colours.conf\nbackground #101014\n",
        );
        assert!(kitty_includes(&main).is_empty());
        // Includes nest against the file holding the line, and cycles stop.
        let leaf = write_file(&home.path().join("sub/leaf.conf"), KITTY);
        let mid = write_file(&home.path().join("sub/mid.conf"), "include leaf.conf\n");
        let main = write_file(&home.path().join("top.conf"), "include sub/mid.conf\n");
        assert_eq!(kitty_includes(&main), vec![mid, leaf]);
        let a = home.path().join("ca.conf");
        let b = home.path().join("cb.conf");
        write_file(&a, "include cb.conf\n");
        write_file(&b, "include ca.conf\n");
        assert_eq!(kitty_includes(&a), vec![b]);
    }

    #[test]
    fn kitty_globinclude_expands_sorted_files() {
        let home = temp_dir("detect-kitty-glob");
        let _guard = lock_env(&[("XDG_CONFIG_HOME", home.root().as_str())]);
        let b = write_file(&home.path().join("parts/b.conf"), KITTY);
        let a = write_file(&home.path().join("parts/a.conf"), "font_size 12\n");
        std::fs::write(home.path().join("parts/notes.txt"), "x\n").unwrap();
        let main = write_file(&home.path().join("main.conf"), "globinclude parts/*.conf\n");
        // Sorted, files only, pattern-scoped — and the colourless half
        // does not hide the half carrying colours.
        assert_eq!(kitty_includes(&main), vec![a.clone(), b.clone()]);
        assert!(config_holds_colours::<Fake>("kitty", &main));
        // `?` and `[...]` work; no match is empty, never an error.
        let main = write_file(&home.path().join("q.conf"), "globinclude parts/?.conf\n");
        assert_eq!(kitty_includes(&main), vec![a.clone(), b.clone()]);
        let main = write_file(&home.path().join("c.conf"), "globinclude parts/[a].conf\n");
        assert_eq!(kitty_includes(&main), vec![a]);
        let main = write_file(&home.path().join("n.conf"), "globinclude parts/*.toml\n");
        assert!(kitty_includes(&main).is_empty());
        assert!(!config_holds_colours::<Fake>("kitty", &main));
    }

    #[test]
    fn kitty_split_config_resolves_to_the_include() {
        let home = temp_dir("detect-kitty-resolve");
        let root = home.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str())]);
        let colours = home.write("kitty/colours.conf", KITTY);
        home.write("kitty/kitty.conf", "font_size 12\ninclude colours.conf\n");
        // Detection follows the include; the push target stays the
        // resolved file (decision 51) — the same answer both modes give.
        let (fmt, target, error) = resolve::<Fake>(Some("kitty"), None);
        assert_eq!(fmt.as_deref(), Some("kitty"));
        assert_eq!(target, Some(colours.clone()));
        assert_eq!(error, None);
        let (_, push_target, _) = resolve_for_push::<Fake>(Some("kitty"), None);
        assert_eq!(push_target, Some(colours));
    }

    // -- decision 51: alacritty imports -----------------------------------

    /// Colours iff the file carries the marker: stands in for a real
    /// Alacritty reader (the test fake has none).
    struct Marked;

    impl FormatStub for Marked {
        fn format_names() -> &'static [&'static str] {
            &["alacritty"]
        }
        fn default_paths(fmt: &str) -> Vec<String> {
            match fmt {
                "alacritty" => vec![format!(
                    "{}/alacritty/alacritty.toml",
                    std::env::var("XDG_CONFIG_HOME").unwrap_or_default()
                )],
                _ => vec![],
            }
        }
        fn env_table(_fmt: &str) -> &'static [(&'static str, &'static str)] {
            &[]
        }
        fn read(_fmt: &str, path: &Path) -> crate::library::SlotMap {
            let mut slots = crate::library::SlotMap::new();
            if std::fs::read_to_string(path)
                .map(|text| text.contains("MARK"))
                .unwrap_or(false)
            {
                slots.insert("background".to_owned(), "#101014".to_owned());
            }
            slots
        }
        fn write(
            _fmt: &str,
            _path: &Path,
            _slots: &crate::library::SlotMap,
        ) -> std::io::Result<()> {
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "test stub has no writer",
            ))
        }
        fn color_line(_fmt: &str, _line: &str) -> bool {
            false
        }
    }

    #[test]
    fn alacritty_imports_cases() {
        let home = temp_dir("detect-alacritty-imports");
        let _guard = lock_env(&[("XDG_CONFIG_HOME", home.root().as_str())]);
        let first = write_file(&home.path().join("first.toml"), "# MARK\n");
        let second = write_file(&home.path().join("sub/second.toml"), "# MARK\n");
        // Under `[general]`, single-line and multi-line.
        let main = write_file(
            &home.path().join("main.toml"),
            &format!(
                "[general]\nimport = [\"{}\", \"sub/second.toml\"]\n",
                first.display()
            ),
        );
        assert_eq!(
            alacritty_imports(&main),
            vec![first.clone(), second.clone()]
        );
        assert!(config_holds_colours::<Marked>("alacritty", &main));
        let main = write_file(
            &home.path().join("multi.toml"),
            "[general]\nimport = [\n  \"first.toml\",\n  # a comment\n]\n",
        );
        assert_eq!(alacritty_imports(&main), vec![first.clone()]);
        // A top-level dotted key works; other sections' `import` is ignored.
        let main = write_file(
            &home.path().join("dotted.toml"),
            "general.import = [\"first.toml\"]\n",
        );
        assert_eq!(alacritty_imports(&main), vec![first.clone()]);
        let main = write_file(
            &home.path().join("other.toml"),
            "[colors]\nimport = [\"first.toml\"]\n",
        );
        assert!(alacritty_imports(&main).is_empty());
        // Missing files are skipped, never an error; cycles stop.
        let main = write_file(
            &home.path().join("gone.toml"),
            "[general]\nimport = [\"gone.toml\", \"first.toml\"]\n",
        );
        assert_eq!(alacritty_imports(&main), vec![first.clone()]);
        let a = home.path().join("ia.toml");
        let b = home.path().join("ib.toml");
        write_file(&a, "[general]\nimport = [\"ib.toml\"]\n");
        write_file(&b, "[general]\nimport = [\"ia.toml\"]\n");
        assert_eq!(alacritty_imports(&a), vec![b]);
        // Imports nest against the file holding the entry.
        let leaf = write_file(&home.path().join("deep/leaf.toml"), "# MARK\n");
        write_file(
            &home.path().join("deep/mid.toml"),
            "[general]\nimport = [\"leaf.toml\"]\n",
        );
        let main = write_file(
            &home.path().join("top.toml"),
            "[general]\nimport = [\"deep/mid.toml\"]\n",
        );
        assert_eq!(
            alacritty_imports(&main),
            vec![home.path().join("deep/mid.toml"), leaf]
        );
        assert!(config_holds_colours::<Marked>("alacritty", &main));
    }

    #[test]
    fn alacritty_split_config_resolves_to_the_import() {
        let home = temp_dir("detect-alacritty-resolve");
        let root = home.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str())]);
        let colours = home.write("alacritty/colours.toml", "# MARK\n");
        home.write(
            "alacritty/alacritty.toml",
            "[general]\nimport = [\"colours.toml\"]\n",
        );
        // Detection follows the import; the push target stays the resolved
        // file (decision 51).
        let (fmt, target, error) = resolve::<Marked>(Some("alacritty"), None);
        assert_eq!(fmt.as_deref(), Some("alacritty"));
        assert_eq!(target, Some(colours));
        assert_eq!(error, None);
    }

    // -- decision 54: push mode excludes /etc ------------------------------

    #[test]
    fn system_paths_are_etc_only() {
        let _guard = lock_env(&[]);
        assert!(is_system_path(Path::new("/etc/kitty/kitty.conf")));
        assert!(is_system_path(Path::new("/etc")));
        assert!(!is_system_path(Path::new("/etcfoo/kitty.conf")));
        assert!(!is_system_path(Path::new("etc/kitty.conf")));
        assert!(!is_system_path(Path::new(
            "/home/you/.config/kitty/kitty.conf"
        )));
    }

    /// Colours in any file that exists: proves detection keeps `/etc`
    /// stops while push mode skips them, end to end.
    struct AnyFile;

    impl FormatStub for AnyFile {
        fn format_names() -> &'static [&'static str] {
            &["kitty"]
        }
        fn default_paths(fmt: &str) -> Vec<String> {
            match fmt {
                "kitty" => vec![
                    "/etc/hosts".to_owned(),
                    "/etc/hostname".to_owned(),
                    "/etc/resolv.conf".to_owned(),
                    format!(
                        "{}/kitty/kitty.conf",
                        std::env::var("XDG_CONFIG_HOME").unwrap_or_default()
                    ),
                ],
                _ => vec![],
            }
        }
        fn env_table(_fmt: &str) -> &'static [(&'static str, &'static str)] {
            &[]
        }
        fn read(_fmt: &str, path: &Path) -> crate::library::SlotMap {
            let mut slots = crate::library::SlotMap::new();
            if path.is_file() {
                slots.insert("background".to_owned(), "#101014".to_owned());
            }
            slots
        }
        fn write(
            _fmt: &str,
            _path: &Path,
            _slots: &crate::library::SlotMap,
        ) -> std::io::Result<()> {
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "test stub has no writer",
            ))
        }
        fn color_line(_fmt: &str, _line: &str) -> bool {
            false
        }
    }

    #[test]
    fn push_mode_skips_etc_stops() {
        let home = temp_dir("detect-push-mode");
        let root = home.root();
        let _guard = lock_env(&[("XDG_CONFIG_HOME", root.as_str())]);
        // The `/etc` stop must exist to be a candidate at all; on a
        // machine with no `/etc` file present there is nothing to filter.
        let etc = ["/etc/hosts", "/etc/hostname", "/etc/resolv.conf"]
            .iter()
            .map(PathBuf::from)
            .find(|path| path.is_file());
        let Some(etc) = etc else {
            return;
        };
        let user = home.path().join("kitty/kitty.conf");
        // With no user config, detection keeps the system stop …
        let (fmt, target, error) = resolve::<AnyFile>(Some("kitty"), None);
        assert_eq!(fmt.as_deref(), Some("kitty"));
        assert_eq!(target, Some(etc.clone()));
        assert_eq!(error, None);
        // …while push mode excludes it: no target, an honest error.
        let (fmt, target, error) = resolve_for_push::<AnyFile>(Some("kitty"), None);
        assert_eq!(fmt.as_deref(), Some("kitty"));
        assert_eq!(target, None);
        assert_eq!(error.as_deref(), Some("no kitty config with colours found"));
        // A user config answers in both modes (kitty's XDG root leads).
        write_file(&user, KITTY);
        let (fmt, target, error) = resolve::<AnyFile>(Some("kitty"), None);
        assert_eq!(fmt.as_deref(), Some("kitty"));
        assert_eq!(target, Some(user.clone()));
        assert_eq!(error, None);
        let (fmt, target, error) = resolve_for_push::<AnyFile>(Some("kitty"), None);
        assert_eq!(fmt.as_deref(), Some("kitty"));
        assert_eq!(target, Some(user.clone()));
        assert_eq!(error, None);
        // An explicit `--config` under `/etc` is refused for push but
        // still resolves for detection.
        let (_, target, error) = resolve::<AnyFile>(Some("kitty"), Some(&etc));
        assert_eq!(target, Some(etc.clone()));
        assert_eq!(error, None);
        let (fmt, target, error) = resolve_for_push::<AnyFile>(Some("kitty"), Some(&etc));
        assert_eq!(fmt.as_deref(), Some("kitty"));
        assert_eq!(target, None);
        assert_eq!(
            error,
            Some(format!(
                "refusing to push to system path: {}",
                etc.display()
            ))
        );
        // …while an explicit user path pushes unchanged.
        let (_, target, error) = resolve_for_push::<AnyFile>(Some("kitty"), Some(&user));
        assert_eq!(target, Some(user));
        assert_eq!(error, None);
    }
}
