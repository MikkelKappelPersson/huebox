//! Providers, detection and the theme library: the facades callers use.
//!
//! Rust port of `huebox/providers.py`, `huebox/detect.py` and
//! `huebox/themes.py` (spec `docs/004-rust-rewrite/spec.md` §7, behaviour
//! from `docs/001-spec/spec.md` §§2, 7). Data flows one way:
//!
//! ```text
//! terminal config → canonical slots → edit buffer → truth file → push to terminal
//! ```
//!
//! - [`providers`]: the `PROVIDERS` registry — list/read provider theme
//!   files through it, never a directory walk of your own.
//! - [`detect`]: probes, candidate paths, Ghostty `config-file` includes /
//!   `theme =` reads, the pointer writer, and [`detect::resolve`].
//! - [`themes`]: home, `state.toml`, theme files, the canonical writer +
//!   subset reader, [`themes::push`] (+ `PushResult.overrides`), post-push
//!   reload, Ghostty native export, overrides, `RAMP`.
//!
//! The dependency rule holds: this crate reaches the terminal formats only
//! through [`format::FormatStub`], never around it.

#![warn(missing_docs)]

use std::collections::BTreeMap;

pub mod backend;
pub mod detect;
pub mod format;
pub mod providers;
pub mod themes;

#[cfg(test)]
mod testutil;

/// Canonical slot values: slot name → lowercase `#rrggbb`.
pub type SlotMap = BTreeMap<String, String>;

/// The 22 canonical slots in order: 16 palette entries, then 6 named.
/// Mirrors `huebox/color.py` `SLOTS` (imported by `themes.py`).
pub const SLOTS: [&str; 22] = [
    "palette-0",
    "palette-1",
    "palette-2",
    "palette-3",
    "palette-4",
    "palette-5",
    "palette-6",
    "palette-7",
    "palette-8",
    "palette-9",
    "palette-10",
    "palette-11",
    "palette-12",
    "palette-13",
    "palette-14",
    "palette-15",
    "background",
    "foreground",
    "cursor-color",
    "cursor-text",
    "selection-background",
    "selection-foreground",
];

/// Fill for slots a source omits. Mirrors `huebox/color.py` `MISSING`.
pub const MISSING: &str = "#808080";

/// Anything the user has to fix: bad name, missing theme, no clobber.
/// Mirrors `themes.ThemeError` (one class, message only).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ThemeError(pub String);

impl std::fmt::Display for ThemeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ThemeError {}

/// Mirrors `color.is_hex`: optional single `#`, six hex digits, surrounding
/// whitespace tolerated.
pub fn is_hex(value: &str) -> bool {
    let text = value.trim();
    let body = text.strip_prefix('#').unwrap_or(text);
    body.len() == 6 && body.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Mirrors `color.normalize_hex`: `#` + stripped + lowercased.
pub fn normalize_hex(value: &str) -> String {
    let mut out = String::with_capacity(7);
    out.push('#');
    out.extend(
        value
            .trim()
            .trim_start_matches('#')
            .chars()
            .map(|c| c.to_ascii_lowercase()),
    );
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_predicate_and_normalisation() {
        assert!(is_hex("#0A0B0C"));
        assert!(is_hex("0a0b0c"));
        assert!(is_hex("  #0a0b0c  "));
        assert!(!is_hex(""));
        assert!(!is_hex("#abc"));
        assert!(!is_hex("##0a0b0c"));
        assert!(!is_hex("chartreuse"));
        assert!(!is_hex("0x101014"));
        assert_eq!(normalize_hex("  #0A0B0C  "), "#0a0b0c");
        assert_eq!(normalize_hex("0A0B0C"), "#0a0b0c");
        assert_eq!(normalize_hex("##0a0b0c"), "#0a0b0c");
    }

    #[test]
    fn slot_order_matches_core() {
        // The single source of canonical order is huebox-core's Slots;
        // this table must agree with it slot for slot.
        let core: Vec<&str> = huebox_core::Slots::default()
            .iter()
            .map(|(name, _)| name)
            .collect();
        assert_eq!(core, SLOTS.to_vec());
        assert_eq!(SLOTS.len(), 22);
    }
}
