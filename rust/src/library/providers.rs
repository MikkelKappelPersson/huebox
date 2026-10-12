//! Provider themes: a `FORMATS`-shaped facade over on-disk theme files.
//!
//! Rust port of `huebox/providers.py`. Lists provider theme files and reads
//! them through [`crate::library::format::FormatStub`], so dialect coverage is
//! inherited, never re-implemented. No subprocess, no network.
//!
//! Discovery is non-recursive with one suffix rule per provider; dotfiles
//! are never themes; a missing/unreadable dir is the empty group; a file
//! with zero colours never lists (skipped per-file with a note).

use std::path::{Path, PathBuf};

use crate::library::format::FormatStub;
use crate::library::SlotMap;

/// Longest legal huebox name (`themes::valid_name`): 64 characters.
pub const MAX_NAME_LEN: usize = 64;
/// Chars a slugified stem leaves for a `-N` batch suffix.
const SUFFIX_ROOM: usize = 2;

/// One `PROVIDERS` entry: the `FORMATS`-mirroring shape
/// (`list` / `read` / `format`), so callers never walk a dir of their own.
pub struct ProviderDef {
    /// Registry key (`ghostty`, `kitty`, `alacritty`).
    pub name: &'static str,
    /// Format its files read through.
    pub format: &'static str,
    /// Accepted filename suffixes (empty: extensionless only).
    pub suffixes: &'static [&'static str],
}

/// The `PROVIDERS` registry. List and read through it, never around it.
pub static PROVIDERS: &[ProviderDef; 3] = &[
    ProviderDef {
        name: "ghostty",
        format: "ghostty",
        suffixes: &[],
    },
    ProviderDef {
        name: "kitty",
        format: "kitty",
        suffixes: &[".conf"],
    },
    ProviderDef {
        name: "alacritty",
        format: "alacritty",
        suffixes: &[".toml", ".yml", ".yaml"],
    },
];

/// Registry keys in order.
pub static PROVIDER_NAMES: &[&str; 3] = &["ghostty", "kitty", "alacritty"];

/// `(entries, notes)` listing shape: rows plus per-file skip notes.
pub type ProviderListing = (Vec<(String, PathBuf)>, Vec<String>);

/// The `PROVIDERS` entry for `provider`, or a usage error.
pub fn known(provider: &str) -> Result<&'static ProviderDef, String> {
    PROVIDERS
        .iter()
        .find(|spec| spec.name == provider)
        .ok_or_else(|| {
            format!(
                "unknown provider '{}' (choose from {})",
                provider,
                PROVIDER_NAMES.join(", ")
            )
        })
}

/// Map a provider theme name to a huebox-name candidate (§8.4).
///
/// Lowercase, runs of non-`[a-z0-9]` collapse to one `-`, leading `-`
/// stripped, stem cut to leave room for a `-N` suffix within 64 chars.
/// Pure: a clash with the library is the caller's to report, never
/// renamed here. Returns `""` when nothing survives.
pub fn slugify(name: &str) -> String {
    let mut slug = String::new();
    let mut dash = false;
    for ch in name.chars() {
        for lower in ch.to_lowercase() {
            if lower.is_ascii_alphanumeric() {
                slug.push(lower);
                dash = false;
            } else if !dash {
                slug.push('-');
                dash = true;
            }
        }
    }
    let slug = slug.trim_start_matches('-');
    slug.chars().take(MAX_NAME_LEN - SUFFIX_ROOM).collect()
}

/// First free slug for `base`: `base`, else `base-2`, `base-3`, ….
///
/// `taken_lower` holds the lowercased names this batch already claimed;
/// the returned candidate (lowercased) is added to it. An empty `base`
/// falls back to `"theme"`. Case-insensitive like `themes::create`.
pub fn unique_slug(base: &str, taken_lower: &mut std::collections::HashSet<String>) -> String {
    let stem: String = if base.is_empty() {
        "theme".to_owned()
    } else {
        base.to_owned()
    };
    let stem: String = stem.chars().take(MAX_NAME_LEN).collect();
    let mut candidate = stem.clone();
    let mut number = 1;
    while taken_lower.contains(&candidate.to_lowercase()) {
        number += 1;
        let suffix = format!("-{number}");
        let keep = MAX_NAME_LEN - suffix.len();
        let head: String = stem.chars().take(keep).collect();
        candidate = format!("{head}{suffix}");
    }
    taken_lower.insert(candidate.to_lowercase());
    candidate
}

/// `unique_slug` over `bases` in order; returns the candidates.
pub fn batch_unique(bases: &[&str]) -> Vec<String> {
    let mut taken = std::collections::HashSet::new();
    bases
        .iter()
        .map(|base| unique_slug(base, &mut taken))
        .collect()
}

/// The list-row name for `filename`, or `None` when no theme file.
fn display_name(provider: &ProviderDef, filename: &str) -> Option<String> {
    if filename.starts_with('.') {
        return None;
    }
    if provider.name == "ghostty" {
        // Extensionless only: Ghostty theme files are `themes/<name>`
        // with spaces allowed — any `.` disqualifies, as
        // `splitext` would report a suffix (even for `"foo."`).
        if filename.contains('.') {
            return None;
        }
        return Some(filename.to_owned());
    }
    let lowered = filename.to_lowercase();
    for suffix in provider.suffixes {
        if lowered.ends_with(suffix) && filename.len() > suffix.len() {
            return Some(filename[..filename.len() - suffix.len()].to_owned());
        }
    }
    None
}

/// `(entries, notes)` for `provider` across `dirs`, in order.
///
/// `entries` is `[(display_name, path)]` sorted by name (case-folded), one
/// row per theme file that holds colours; `notes` names each skipped file
/// (`<path>: no colours found, skipped`). A missing/unreadable dir
/// contributes nothing; a name in two dirs resolves to the later dir.
/// `dirs=None` lists nothing.
pub fn list_themes<F: FormatStub>(
    provider: &str,
    dirs: Option<&[PathBuf]>,
) -> Result<ProviderListing, String> {
    let spec = known(provider)?;
    // One row per display name; a name in two dirs resolves to the later
    // dir (the user dir shadows shipped). Keyed EXACT-case like Python's
    // `found[display] = path` — names differing only by case list twice;
    // the final sort below is the only case-folded step.
    let mut found: std::collections::HashMap<String, PathBuf> = std::collections::HashMap::new();
    let mut notes = Vec::new();
    for directory in dirs.unwrap_or(&[]) {
        let entries = match std::fs::read_dir(directory) {
            Ok(handle) => handle,
            Err(_) => continue,
        };
        let mut filenames: Vec<String> = Vec::new();
        for entry in entries {
            let entry = match entry {
                Ok(entry) => entry,
                Err(_) => continue,
            };
            filenames.push(entry.file_name().to_string_lossy().into_owned());
        }
        filenames.sort();
        for filename in filenames {
            let display = match display_name(spec, &filename) {
                Some(display) => display,
                None => continue,
            };
            let path = directory.join(&filename);
            if !path.is_file() {
                continue;
            }
            if F::read(spec.format, &path).is_empty() {
                notes.push(format!("{}: no colours found, skipped", path.display()));
                continue;
            }
            found.insert(display, path);
        }
    }
    let mut entries: Vec<(String, PathBuf)> = found.into_iter().collect();
    entries.sort_by_key(|entry| entry.0.to_lowercase());
    Ok((entries, notes))
}

/// Slots for one provider file via the format backend; empty when none.
pub fn read_theme<F: FormatStub>(provider: &str, path: &Path) -> Result<SlotMap, String> {
    Ok(F::read(known(provider)?.format, path))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::library::testutil::{lock_env, temp_dir};

    /// Test double for [`FormatStub`]: ghostty + kitty flat dialects with
    /// rule fidelity (span-preserving splice, no-op keeps mtime);
    /// alacritty reads empty (wired to the real format at merge).
    #[derive(Clone, Copy)]
    pub(crate) struct Mem;

    fn norm(token: &str) -> String {
        format!(
            "#{}",
            token.trim().trim_start_matches('#').to_ascii_lowercase()
        )
    }

    /// Match one rule against a line; returns (slot, val_start, val_end).
    fn match_ghostty(line: &str) -> Option<(String, usize, usize)> {
        let bytes = line.as_bytes();
        let mut i = 0;
        while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
            i += 1;
        }
        let rest = &line[i..];
        let lower = rest.to_lowercase();
        if let Some(tail) = lower.strip_prefix("palette") {
            // \s*=\s* index \s*=\s* val (tail-relative)
            let mut j = 0;
            let b = tail.as_bytes();
            while j < b.len() && (b[j] == b' ' || b[j] == b'\t') {
                j += 1;
            }
            if b.get(j) != Some(&b'=') {
                return None;
            }
            j += 1;
            while j < b.len() && (b[j] == b' ' || b[j] == b'\t') {
                j += 1;
            }
            let digits = j;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
            if j == digits {
                return None;
            }
            let index: String = tail[digits..j].to_owned();
            while j < b.len() && (b[j] == b' ' || b[j] == b'\t') {
                j += 1;
            }
            if b.get(j) != Some(&b'=') {
                return None;
            }
            j += 1;
            while j < b.len() && (b[j] == b' ' || b[j] == b'\t') {
                j += 1;
            }
            let vstart = i + 7 + j;
            let lb = line.as_bytes();
            let mut k = vstart;
            if lb.get(k) == Some(&b'#') {
                k += 1;
            }
            let hex_start = k;
            while k < lb.len() && (lb[k] as char).is_ascii_hexdigit() {
                k += 1;
            }
            if k - hex_start != 6 {
                return None;
            }
            if k < lb.len() && ((lb[k] as char).is_ascii_alphanumeric() || lb[k] == b'_') {
                return None; // \b
            }
            return Some((format!("palette-{index}"), vstart, k));
        }
        for name in [
            "background",
            "foreground",
            "cursor-color",
            "cursor-text",
            "selection-background",
            "selection-foreground",
        ] {
            if let Some(tail) = lower.strip_prefix(name) {
                // \s*=\s* val  (= required for ghostty named)
                let b = tail.as_bytes();
                let mut j = 0;
                while j < b.len() && (b[j] == b' ' || b[j] == b'\t') {
                    j += 1;
                }
                if b.get(j) != Some(&b'=') {
                    break;
                }
                j += 1;
                while j < b.len() && (b[j] == b' ' || b[j] == b'\t') {
                    j += 1;
                }
                let vstart = i + name.len() + j;
                let lb = line.as_bytes();
                let mut k = vstart;
                if lb.get(k) == Some(&b'#') {
                    k += 1;
                }
                let hex_start = k;
                while k < lb.len() && (lb[k] as char).is_ascii_hexdigit() {
                    k += 1;
                }
                if k - hex_start != 6 {
                    break;
                }
                if k < lb.len() && ((lb[k] as char).is_ascii_alphanumeric() || lb[k] == b'_') {
                    break;
                }
                return Some((name.to_owned(), vstart, k));
            }
        }
        None
    }

    fn match_kitty(line: &str) -> Option<(String, usize, usize)> {
        let bytes = line.as_bytes();
        let mut i = 0;
        while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
            i += 1;
        }
        let rest = &line[i..];
        let lower = rest.to_lowercase();
        let (slot, after): (String, usize) = if let Some(tail) = lower.strip_prefix("color") {
            let b = tail.as_bytes();
            let mut j = 0;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
            if j == 0 {
                return None;
            }
            (format!("palette-{}", &tail[..j]), 5 + j)
        } else {
            let mut hit = None;
            for name in [
                "background",
                "foreground",
                "cursor",
                "selection_background",
                "selection_foreground",
            ] {
                if let Some(after) = lower.strip_prefix(name) {
                    // word boundary: next char must not extend the name
                    if after.starts_with(|c: char| c.is_ascii_alphanumeric() || c == '_') {
                        continue;
                    }
                    let slot = match name {
                        "cursor" => "cursor-color",
                        "selection_background" => "selection-background",
                        "selection_foreground" => "selection-foreground",
                        other => other,
                    };
                    hit = Some((slot.to_owned(), name.len()));
                    break;
                }
            }
            hit?
        };
        // \s*=?\s* val
        let b = rest.as_bytes();
        let mut j = after;
        while j < b.len() && (b[j] == b' ' || b[j] == b'\t') {
            j += 1;
        }
        if b.get(j) == Some(&b'=') {
            j += 1;
        }
        while j < b.len() && (b[j] == b' ' || b[j] == b'\t') {
            j += 1;
        }
        let vstart = i + j;
        let lb = line.as_bytes();
        let mut k = vstart;
        if lb.get(k) == Some(&b'#') {
            k += 1;
        }
        let hex_start = k;
        while k < lb.len() && (lb[k] as char).is_ascii_hexdigit() {
            k += 1;
        }
        if k - hex_start != 6 {
            return None;
        }
        if k < lb.len() && ((lb[k] as char).is_ascii_alphanumeric() || lb[k] == b'_') {
            return None;
        }
        Some((slot, vstart, k))
    }

    pub(crate) fn read_flat(path: &Path, ghostty: bool) -> SlotMap {
        let text = match std::fs::read_to_string(path) {
            Ok(text) => text,
            Err(_) => return SlotMap::new(),
        };
        let mut slots = SlotMap::new();
        for line in text.split('\n') {
            // splitlines: strip a trailing \r like Python str.splitlines
            let line = line.strip_suffix('\r').unwrap_or(line);
            let hit = if ghostty {
                match_ghostty(line)
            } else {
                match_kitty(line)
            };
            if let Some((slot, start, end)) = hit {
                slots.insert(slot, norm(&line[start..end]));
            }
        }
        slots
    }

    pub(crate) fn write_flat(path: &Path, slots: &SlotMap, ghostty: bool) -> std::io::Result<()> {
        let before: Vec<String> = std::fs::read_to_string(path)?
            .lines()
            .map(str::to_owned)
            .collect();
        let mut lines = before.clone();
        for line in &mut lines {
            let hit = if ghostty {
                match_ghostty(line)
            } else {
                match_kitty(line)
            };
            if let Some((slot, start, end)) = hit {
                if let Some(value) = slots.get(&slot) {
                    line.replace_range(start..end, value);
                }
            }
        }
        if lines == before {
            return Ok(());
        }
        std::fs::write(path, lines.join("\n") + "\n")
    }

    impl crate::library::format::FormatStub for Mem {
        fn format_names() -> &'static [&'static str] {
            &["ghostty", "kitty", "alacritty"]
        }
        fn default_paths(fmt: &str) -> Vec<String> {
            match fmt {
                "ghostty" => vec![
                    "~/.config/ghostty/config.ghostty".to_owned(),
                    "~/.config/ghostty/config".to_owned(),
                ],
                "kitty" => vec![
                    "~/.config/kitty/kitty.conf".to_owned(),
                    "~/.kitty.conf".to_owned(),
                ],
                "alacritty" => vec![
                    "~/.config/alacritty/alacritty.toml".to_owned(),
                    "~/.config/alacritty.toml".to_owned(),
                    "~/.config/alacritty/alacritty.yml".to_owned(),
                    "~/.alacritty.toml".to_owned(),
                ],
                _ => vec![],
            }
        }
        fn env_table(fmt: &str) -> &'static [(&'static str, &'static str)] {
            match fmt {
                "kitty" => &[
                    ("KITTY_CONFIG_DIRECTORY", "kitty.conf"),
                    ("KITTY_CONFIG_DIR", "kitty.conf"),
                ],
                _ => &[],
            }
        }
        fn read(fmt: &str, path: &Path) -> SlotMap {
            match fmt {
                "ghostty" => read_flat(path, true),
                "kitty" => read_flat(path, false),
                _ => SlotMap::new(),
            }
        }
        fn write(fmt: &str, path: &Path, slots: &SlotMap) -> std::io::Result<()> {
            match fmt {
                "ghostty" => write_flat(path, slots, true),
                "kitty" => write_flat(path, slots, false),
                _ => Err(std::io::Error::new(
                    std::io::ErrorKind::Unsupported,
                    format!("test fake has no writer for {fmt}"),
                )),
            }
        }
        fn color_line(fmt: &str, line: &str) -> bool {
            fmt == "ghostty" && match_ghostty(line).is_some()
        }
    }

    pub(crate) use Mem as Fake;

    fn write_file(path: &Path, text: &str) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, text).unwrap();
    }

    #[test]
    fn slugify_cases() {
        let _guard = lock_env(&[]);
        assert_eq!(slugify("Catppuccin Mocha"), "catppuccin-mocha");
        assert_eq!(slugify("  --Foo"), "foo");
        assert_eq!(slugify("!!!"), "");
        assert_eq!(slugify(&"a".repeat(100)).len(), MAX_NAME_LEN - 2);
    }

    #[test]
    fn batch_unique_cases() {
        let _guard = lock_env(&[]);
        assert_eq!(
            batch_unique(&["ember", "ember", "ember"]),
            vec!["ember", "ember-2", "ember-3"]
        );
        let names = batch_unique(&[&"a".repeat(64), &"a".repeat(64)]);
        assert_eq!(names, vec!["a".repeat(64), format!("{}-2", "a".repeat(62))]);
        for name in &names {
            assert!(crate::library::themes::valid_name(name), "{name}");
        }
    }

    #[test]
    fn missing_and_file_dirs_list_empty() {
        let _guard = lock_env(&[]);
        let dir = temp_dir("providers-empty");
        assert_eq!(
            list_themes::<Mem>("ghostty", Some(&[dir.path().join("nope")])).unwrap(),
            (vec![], vec![])
        );
        let file = dir.path().join("kitty.conf");
        write_file(&file, "background #101010\n");
        assert_eq!(
            list_themes::<Mem>("kitty", Some(std::slice::from_ref(&file)))
                .unwrap()
                .0,
            vec![]
        );
        assert_eq!(
            list_themes::<Mem>("ghostty", None).unwrap(),
            (vec![], vec![])
        );
    }

    #[test]
    fn unreadable_dir_lists_empty() {
        let _guard = lock_env(&[]);
        // No mock seam in Rust: a file where a dir belongs is the honest
        // equivalent — read_dir fails, the group is empty, no exception.
        let dir = temp_dir("providers-unreadable");
        let file = dir.path().join("x");
        write_file(&file, "background #101010\n");
        assert_eq!(
            list_themes::<Mem>("ghostty", Some(std::slice::from_ref(&file))).unwrap(),
            (vec![], vec![])
        );
    }

    #[test]
    fn zero_colour_file_skipped_with_note() {
        let _guard = lock_env(&[]);
        let dir = temp_dir("providers-zero");
        write_file(&dir.path().join("Empty"), "font-family = x\n");
        let (entries, notes) =
            list_themes::<Mem>("ghostty", Some(std::slice::from_ref(&dir.path_buf()))).unwrap();
        assert_eq!(entries, vec![]);
        assert_eq!(notes.len(), 1);
        assert!(notes[0].contains("Empty"), "{}", notes[0]);
    }

    #[test]
    fn suffix_dotfile_shadow_and_sort_rules() {
        let _guard = lock_env(&[]);
        let ship = temp_dir("providers-ship");
        let user = temp_dir("providers-user");
        write_file(&ship.path().join("Ember"), "background = #101010\n");
        write_file(&user.path().join("Ember"), "background = #202020\n");
        write_file(&user.path().join("gruvbox"), "background = #303030\n");
        write_file(&user.path().join(".hidden"), "background = #404040\n");
        write_file(&user.path().join("notes.txt"), "background = #505050\n");
        let (entries, _) =
            list_themes::<Mem>("ghostty", Some(&[ship.path_buf(), user.path_buf()])).unwrap();
        // Later dir shadows; dotfiles and suffixed names never list;
        // sorted case-folded.
        assert_eq!(
            entries
                .iter()
                .map(|(name, _)| name.as_str())
                .collect::<Vec<_>>(),
            vec!["Ember", "gruvbox"]
        );
        assert!(entries[0].1.starts_with(user.path()));
        // kitty takes .conf only; alacritty .toml/.yml/.yaml.
        let kdir = temp_dir("providers-kitty");
        write_file(&kdir.path().join("a.conf"), "background #101010\n");
        write_file(&kdir.path().join("a.toml"), "background #101010\n");
        write_file(&kdir.path().join(".b.conf"), "background #101010\n");
        let (kentries, _) =
            list_themes::<Mem>("kitty", Some(std::slice::from_ref(&kdir.path_buf()))).unwrap();
        assert_eq!(kentries.len(), 1);
        assert_eq!(kentries[0].0, "a");
        // Suffixes are per-provider: only `.conf` lists under kitty…
        let adir = temp_dir("providers-alacritty");
        for name in ["x.toml", "y.yml", "z.yaml", "w.conf"] {
            write_file(&adir.path().join(name), "background #101010\n");
        }
        let (aentries, _) =
            list_themes::<Mem>("kitty", Some(std::slice::from_ref(&adir.path_buf()))).unwrap();
        assert_eq!(
            aentries
                .iter()
                .map(|(name, _)| name.as_str())
                .collect::<Vec<_>>(),
            vec!["w"]
        );
        // …while alacritty takes `.toml` / `.yml` / `.yaml` (empty reads
        // here: the test fake has no TOML reader — wired at merge).
        let (alentries, anotes) =
            list_themes::<Mem>("alacritty", Some(std::slice::from_ref(&adir.path_buf()))).unwrap();
        assert!(alentries.is_empty());
        assert_eq!(anotes.len(), 3);
    }

    #[test]
    fn read_theme_and_registry() {
        let _guard = lock_env(&[]);
        let dir = temp_dir("providers-read");
        let path = dir.path().join("Ember");
        write_file(&path, "background = #101010\n");
        assert_eq!(
            read_theme::<Mem>("ghostty", &path).unwrap()["background"],
            "#101010"
        );
        assert!(read_theme::<Mem>("bogus", &path).is_err());
        assert!(list_themes::<Mem>("bogus", None).is_err());
        assert_eq!(PROVIDER_NAMES, &["ghostty", "kitty", "alacritty"]);
        assert_eq!(known("kitty").unwrap().format, "kitty");
    }
}
