//! Terminal config formats: read slots out of config text, splice them back.
//!
//! Rust port of `huebox/formats/` (001 spec §6): the flat rule machinery
//! plus the Ghostty, kitty and Alacritty dialects behind one registry.
//! This crate depends only on `huebox-core` and `std`, and it performs no
//! file I/O — every function here is a pure text transform. Reading files
//! and deciding when to write them lives in huebox-lib; here `write`
//! returns the full new text and [`apply_write`] decides whether it is
//! worth writing.
//!
//! # Facade
//!
//! Callers use [`read`] and [`write()`] through the [`FORMATS`] registry and
//! never reach into format internals: the `ghostty`, `kitty`, `alacritty`
//! and `base` modules are private. [`FORMAT_NAMES`] lists the registry in
//! probe order; [`lookup`] exposes each format's static data (`defaults`
//! candidate paths, `env` overrides) for huebox-lib's detection pass.
//!
//! # Write contract (001 spec §6.2)
//!
//! Writes are line-level: only colour tokens are replaced, and a write
//! that changes nothing is byte-identical to the input. Compare first,
//! write second: [`apply_write`] returns [`None`] when the new text equals
//! the old, so a no-op never opens the file and never bumps its mtime.
//!
//! The one exception is the Ghostty overrides delete, which removes whole
//! colour lines and leaves every other byte identical. That lives in
//! [`ghostty_remove_color_lines`], not in [`write()`] — a save that shadows
//! its own export deletes through that function, never by matching colour
//! lines itself.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

use std::collections::BTreeMap;

use crate::core::Slots;

mod alacritty;
mod base;
mod ghostty;
mod kitty;

/// One supported terminal format: its name, its static detection data,
/// and its text transforms.
///
/// `defaults` are the candidate config paths in priority order and `env`
/// the environment overrides probed in order (both used by huebox-lib's
/// detection, which owns all file access). The transforms stay private so
/// every read and write goes through [`read`] / [`write()`].
pub struct FormatDef {
    /// Registry name: `ghostty`, `kitty` or `alacritty`.
    pub name: &'static str,
    /// Candidate config paths, in priority order.
    pub defaults: &'static [&'static str],
    /// Env-var overrides, probed in order (`var` → path suffix).
    pub env: &'static [(&'static str, &'static str)],
    read: fn(&str) -> BTreeMap<String, String>,
    write: fn(&str, &Slots) -> String,
}

/// Supported terminal formats in probe order — the `FORMATS` registry.
///
/// Callers dispatch through [`read`] / [`write()`] / [`lookup`], never
/// through a module path.
pub static FORMATS: &[FormatDef] = &[
    FormatDef {
        name: "ghostty",
        defaults: &[
            "~/.config/ghostty/config.ghostty",
            "~/.config/ghostty/config",
        ],
        env: &[],
        read: ghostty::read_text,
        write: ghostty::write_text,
    },
    FormatDef {
        name: "kitty",
        // `KITTY_CONFIG_DIRECTORY` is the spelling upstream documents;
        // `KITTY_CONFIG_DIR` is huebox's own, kept as a secondary probe.
        // Order matters — detection probes the slice in order.
        defaults: &["~/.config/kitty/kitty.conf", "~/.kitty.conf"],
        env: &[
            ("KITTY_CONFIG_DIRECTORY", "kitty.conf"),
            ("KITTY_CONFIG_DIR", "kitty.conf"),
        ],
        read: kitty::read_text,
        write: kitty::write_text,
    },
    FormatDef {
        name: "alacritty",
        // Upstream's own order, with huebox's legacy `alacritty.yml`
        // kept where v1 found it. Alacritty documents no env var for the
        // config path, so there is none to honour here.
        defaults: &[
            "~/.config/alacritty/alacritty.toml",
            "~/.config/alacritty.toml",
            "~/.config/alacritty/alacritty.yml",
            "~/.alacritty.toml",
        ],
        env: &[],
        read: alacritty::read_text,
        write: alacritty::write_text,
    },
];

/// Registry names in probe order (`ghostty`, `kitty`, `alacritty`).
pub const FORMAT_NAMES: [&str; 3] = ["ghostty", "kitty", "alacritty"];

/// The registry entry for `format`.
///
/// Panics on an unknown name — the three [`FORMAT_NAMES`] entries are the
/// whole registry, the way the 22 slot names are the whole colour model.
pub fn lookup(format: &str) -> &'static FormatDef {
    FORMATS
        .iter()
        .find(|def| def.name == format)
        .unwrap_or_else(|| panic!("huebox-format: unknown format {format:?}"))
}

/// Every colour slot `format` finds in `text`, normalised to `#rrggbb`.
///
/// Missing slots are simply absent (an empty map means the text holds no
/// colours); a repeated slot keeps its last value.
pub fn read(format: &str, text: &str) -> BTreeMap<String, String> {
    (lookup(format).read)(text)
}

/// Splice `slots` into `text` line by line and return the full new text.
///
/// The result is byte-identical to `text` when nothing changed — pass both
/// through [`apply_write`] to learn whether a write is owed.
pub fn write(format: &str, text: &str, slots: &Slots) -> String {
    (lookup(format).write)(text, slots)
}

/// Compare first, write second (§6.2 rule 4): [`None`] when `new` is
/// byte-identical to `old` — the caller must not open the file, so a
/// no-op save leaves the config's mtime alone — otherwise the new text.
pub fn apply_write(old: &str, new: &str) -> Option<String> {
    if old == new {
        None
    } else {
        Some(new.to_owned())
    }
}

/// Drop every Ghostty colour line from `text`; returns the kept text and
/// how many lines went.
///
/// The overrides-delete worker: whole-line removal is the one sanctioned
/// exception to token-splice writes, and this function is where it lives.
/// The caller (huebox-lib's `clear_ghostty_overrides` equivalent) writes
/// only when the count is nonzero; comments, non-colour keys and the
/// `theme =` pointer itself survive byte-identical.
pub fn ghostty_remove_color_lines(text: &str) -> (String, usize) {
    ghostty::remove_color_lines(text)
}

/// Whether Ghostty's rules match `line` — the per-line twin of
/// [`ghostty_remove_color_lines`], backing huebox-lib's overrides scan.
/// Same predicate the whole-text remover filters by; kept public so lib
/// never re-implements the rule table.
pub fn ghostty_is_color_line(line: &str) -> bool {
    ghostty::match_line(line).is_some()
}
