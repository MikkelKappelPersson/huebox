//! Bundled themes: shipped inside the binary, read-only.
//!
//! Three canonical huebox themes (`plain-light`, `plain-dark`,
//! `nightspice`) embedded via `include_str!`. Each file is the golden
//! copy for tests: it parses to 22 slots and round-trips byte-identical
//! through [`crate::library::themes::save_with`] with the pinned clock.
//!
//! Nothing ever writes back into the bundle: create and import copy
//! through [`crate::library::themes::create`], so `~/.config/huebox`
//! stays the truth.

use crate::library::SlotMap;

/// Bundled theme names in list order.
pub const NAMES: [&str; 3] = ["plain-light", "plain-dark", "nightspice"];

const PLAIN_LIGHT: &str = include_str!("../bundled/plain-light.toml");
const PLAIN_DARK: &str = include_str!("../bundled/plain-dark.toml");
const NIGHTSPICE: &str = include_str!("../bundled/nightspice.toml");

fn text_of(name: &str) -> Option<&'static str> {
    match name {
        "plain-light" => Some(PLAIN_LIGHT),
        "plain-dark" => Some(PLAIN_DARK),
        "nightspice" => Some(NIGHTSPICE),
        _ => None,
    }
}

/// Names in list order.
pub fn list() -> Vec<String> {
    NAMES.iter().map(|name| name.to_string()).collect()
}

/// Slots for `name`, or `None` for an unknown bundle.
///
/// Parses the embedded `[colors]` block with the same hex rule the theme
/// reader uses (quoted or bare `#rrggbb`, normalised lowercase). Gaps
/// cannot happen — the golden files carry all 22 — but a missing slot
/// would read as `MISSING` grey rather than fail, so a hand-edited bundle
/// still opens.
pub fn read(name: &str) -> Option<SlotMap> {
    let text = text_of(name)?;
    let mut slots = SlotMap::new();
    let mut in_colors = false;
    for raw in text.split('\n') {
        let line = raw.strip_suffix('\r').unwrap_or(raw);
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if trimmed.starts_with('[') {
            in_colors = trimmed == "[colors]";
            continue;
        }
        if !in_colors {
            continue;
        }
        let Some((key, value)) = trimmed.split_once('=') else {
            continue;
        };
        let key = key.trim().trim_matches(['"', '\'']).to_owned();
        let mut value = value.trim();
        // Strip a trailing comment, ignoring a `#` that opens a colour.
        // Keep it tiny: bundled files are machine-written, so a `#`
        // inside quotes is the colour itself.
        if let Some(hash) = value.find('#') {
            let before = &value[..hash];
            // A `#` followed by six hex digits (and a word boundary) is a
            // colour, not a comment — anything else ends the line.
            let after = &value[hash + 1..];
            let is_hex = after.len() >= 6
                && after[..6].bytes().all(|b| b.is_ascii_hexdigit())
                && after[6..]
                    .chars()
                    .next()
                    .is_none_or(|c| !c.is_ascii_alphanumeric() && c != '_');
            if !is_hex {
                value = before;
            }
        }
        let value = value.trim().trim_matches(['"', '\'']).trim();
        if !crate::library::SLOTS.contains(&key.as_str()) {
            continue;
        }
        if crate::library::is_hex(value) {
            slots.insert(key, crate::library::normalize_hex(value));
        }
    }
    // Heal gaps like the theme reader, so the bundle never opens short.
    for slot in crate::library::SLOTS {
        slots
            .entry(slot.to_owned())
            .or_insert_with(|| crate::library::MISSING.to_owned());
    }
    Some(slots)
}

/// Raw embedded text for `name`, or `None`. The golden-copy seam tests
/// compare through.
pub fn source(name: &str) -> Option<&'static str> {
    text_of(name)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::library::testutil::{lock_env, temp_dir};

    #[test]
    fn three_themes_parse_to_22_slots() {
        let _guard = lock_env(&[]);
        assert_eq!(list(), vec!["plain-light", "plain-dark", "nightspice"]);
        for name in list() {
            let slots = read(&name).expect("bundled reads");
            assert_eq!(slots.len(), 22, "{name}");
            for slot in crate::library::SLOTS {
                let value = &slots[slot];
                assert!(crate::library::is_hex(value), "{name} {slot}={value}");
            }
        }
        assert!(read("bogus").is_none());
        assert!(source("plain-light").is_some());
    }

    #[test]
    fn golden_copies_round_trip_byte_identical() {
        let dir = temp_dir("bundled-golden");
        let _guard = lock_env(&[
            ("XDG_CONFIG_HOME", dir.root().as_str()),
            ("HOME", dir.root().as_str()),
        ]);
        let now = "2026-01-01T00:00:00";
        for name in list() {
            let slots = read(&name).expect("bundled reads");
            let path = crate::library::themes::save_with(&name, &slots, None, now).unwrap();
            let written = std::fs::read_to_string(&path).unwrap();
            let golden = source(&name).expect("golden source");
            assert_eq!(written, golden, "{name} round-trips");
        }
    }

    #[test]
    fn distinct_modes() {
        use std::collections::BTreeMap;
        let _guard = lock_env(&[]);
        let light = read("plain-light").unwrap();
        let dark = read("plain-dark").unwrap();
        let spice = read("nightspice").unwrap();
        // Light background is bright, dark ones are not.
        assert_ne!(light["background"], dark["background"]);
        assert_ne!(light["foreground"], dark["foreground"]);
        assert_ne!(spice["background"], dark["background"]);
        // Accent carryover: nightspice cursor differs from the plains.
        let mut plain_cursors = BTreeMap::new();
        plain_cursors.insert("light", light["cursor-color"].clone());
        plain_cursors.insert("dark", dark["cursor-color"].clone());
        assert!(
            !plain_cursors.values().any(|c| c == &spice["cursor-color"]),
            "spice accent carries over"
        );
    }
}
