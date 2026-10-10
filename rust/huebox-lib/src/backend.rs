//! The real [`FormatStub`] backend: [`RealBackend`] wires the seam to
//! `huebox-format`'s text-form registry. Merge-time adapter (P2 trio):
//! file↔text on this side, [`Slots`][huebox_core::Slots]↔[`SlotMap`]
//! conversion in the middle, pure splice underneath.
//!
//! Unknown formats never reach `huebox_format::lookup` (which panics):
//! `read` answers empty like an unreadable file, `write` fails with the
//! same `unknown format 'x' (choose from ...)` message the CLI prints as
//! `huebox: ...` with exit 1 — no tracebacks for user errors.

use std::io;
use std::path::Path;

use huebox_core::Slots;

use crate::format::{unknown_format, FormatStub};
use crate::SlotMap;

/// [`FormatStub`] over the real `huebox-format` registry.
pub struct RealBackend;

fn known(fmt: &str) -> bool {
    huebox_format::FORMAT_NAMES.contains(&fmt)
}

fn to_slots(map: &SlotMap) -> Slots {
    let mut slots = Slots::default();
    for (name, value) in map {
        // Theme/config maps hold canonical names only; anything else is a
        // caller bug, and Slots::set says so loudly.
        slots.set(name, value);
    }
    slots
}

fn read_text_lossy(bytes: &[u8]) -> String {
    match String::from_utf8(bytes.to_vec()) {
        Ok(text) => text,
        // Python reads surrogateescape byte-faithful; lossy is the
        // documented edge (review P2c) — a U+FFFD can misclassify a
        // non-UTF8 colour line, never a UTF-8 one.
        Err(_) => String::from_utf8_lossy(bytes).into_owned(),
    }
}

impl FormatStub for RealBackend {
    fn format_names() -> &'static [&'static str] {
        &huebox_format::FORMAT_NAMES
    }

    fn default_paths(fmt: &str) -> Vec<String> {
        if !known(fmt) {
            return Vec::new();
        }
        huebox_format::lookup(fmt)
            .defaults
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    fn env_table(fmt: &str) -> &'static [(&'static str, &'static str)] {
        if !known(fmt) {
            return &[];
        }
        huebox_format::lookup(fmt).env
    }

    fn read(fmt: &str, path: &Path) -> SlotMap {
        if !known(fmt) {
            return SlotMap::new();
        }
        let bytes = match std::fs::read(path) {
            Ok(bytes) => bytes,
            Err(_) => return SlotMap::new(),
        };
        huebox_format::read(fmt, &read_text_lossy(&bytes))
    }

    fn write(fmt: &str, path: &Path, slots: &SlotMap) -> io::Result<()> {
        if !known(fmt) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                unknown_format(fmt, &huebox_format::FORMAT_NAMES),
            ));
        }
        let old = read_text_lossy(&std::fs::read(path)?);
        let new = huebox_format::write(fmt, &old, &to_slots(slots));
        // Compare first, write second: a no-op never opens the file.
        if let Some(out) = huebox_format::apply_write(&old, &new) {
            std::fs::write(path, out)?;
        }
        Ok(())
    }

    fn color_line(fmt: &str, line: &str) -> bool {
        // Only Ghostty is ever asked (the overrides scan); anything else
        // — including unknown formats — answers false, never panics.
        fmt == "ghostty" && huebox_format::ghostty_is_color_line(line)
    }
}
