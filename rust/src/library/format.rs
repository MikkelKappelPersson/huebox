//! The format seam: what `huebox-lib` needs from `huebox-format`.
//!
//! Python reaches the formats through the `FORMATS` registry
//! (`FORMATS[fmt]["read"]` / `["write"]` / `["defaults"]` / `["env"]`) plus
//! the Ghostty rule table (for the overrides delete). Until the
//! `huebox-format` crate lands, this trait is that registry: every function
//! in [`crate::library::providers`], [`crate::library::detect`] and [`crate::library::themes`] that
//! touches a terminal config is generic over `F: FormatStub`, and the test
//! suite runs them against an in-memory fake. The orchestrator wires the
//! real backend at merge with no signature changes.
//!
//! # Expected real shape (for the orchestrator)
//!
//! The frozen registry the sibling worktree builds against:
//!
//! ```text
//! crate::format::{FORMATS, read(fmt, &str), write(fmt, ...), apply_write}
//! ```
//!
//! Wire it as:
//!
//! ```ignore
//! impl FormatStub for RealBackend {
//!     fn format_names() -> &'static [&'static str] { /* FORMATS keys */ }
//!     fn default_paths(fmt: &str) -> Vec<String> {
//!         /* FORMATS[fmt]["defaults"] verbatim (unexpanded `~` ok) */
//!     }
//!     fn env_table(fmt: &str) -> &'static [(&'static str, &'static str)] {
//!         /* FORMATS[fmt]["env"] as (var, filename) pairs, probe order */
//!     }
//!     fn read(fmt: &str, path: &Path) -> SlotMap {
//!         /* read(fmt, path) — {} when unreadable */
//!     }
//!     fn write(fmt: &str, path: &Path, slots: &SlotMap) -> io::Result<()> {
//!         /* write(fmt, path, slots): line-level splice; a no-op write
//!            must not open the file (mtime untouched) */
//!     }
//!     fn color_line(fmt: &str, line: &str) -> bool {
//!         /* whether any of fmt's rules match line (the overrides delete;
//!            only "ghostty" is ever asked) */
//!     }
//! }
//! ```
//!
//! Notes:
//!
//! - `default_paths` returns the tables verbatim (including the leading
//!   `~/.config/...` spellings); XDG expansion (`_prefer_xdg`) and the
//!   kitty XDG prepend live in [`crate::library::detect`], as in `detect.py`.
//! - `read` must return an empty map (never fail) when the file cannot be
//!   read; `write` must propagate I/O failures (a failed push is a report,
//!   never a rollback).
//! - `apply_write`, if the real crate exposes it as a pure splice, can back
//!   `write`; this trait only needs the file form.
//! - `insert_missing` is a provided method: the default runs the real
//!   [`crate::format::insert_missing`] text transform with the same
//!   compare-first-write-second gate as `write`, so backends inherit it
//!   untouched — override only to model a failing disk.

use std::io;
use std::path::Path;

use crate::library::SlotMap;

/// The `FORMATS`-shaped facade over terminal formats. See the module docs
/// for the wiring contract.
pub trait FormatStub {
    /// Registry keys in canonical order (`FORMAT_NAMES`).
    fn format_names() -> &'static [&'static str];
    /// Candidate-path table, verbatim (`FORMATS[fmt]["defaults"]`).
    fn default_paths(fmt: &str) -> Vec<String>;
    /// Env probes in order (`FORMATS[fmt]["env"]` as `(var, filename)`).
    fn env_table(fmt: &str) -> &'static [(&'static str, &'static str)];
    /// Slots `fmt` finds in `path`; empty when unreadable.
    fn read(fmt: &str, path: &Path) -> SlotMap;
    /// Splice `slots` into `path` line-level; no-op leaves the file alone.
    fn write(fmt: &str, path: &Path, slots: &SlotMap) -> io::Result<()>;
    /// Append one line per slot the file lacks, in canonical order;
    /// returns the slots added (empty when nothing was missing).
    ///
    /// The file-level twin of [`crate::format::insert_missing`]: flat
    /// formats grow after the last colour line, TOML under its
    /// `[colors.*]` table (created at end of file if absent). Compare
    /// first, write second — a file missing nothing keeps its mtime, and
    /// an unknown format adds nothing. The push calls this only for a
    /// target it already resolved, so a system path never reaches here.
    fn insert_missing(fmt: &str, path: &Path, slots: &SlotMap) -> io::Result<Vec<String>> {
        if !crate::format::FORMAT_NAMES.contains(&fmt) {
            return Ok(Vec::new());
        }
        let old = String::from_utf8_lossy(&std::fs::read(path)?).into_owned();
        let mut core = crate::core::Slots::default();
        for slot in crate::library::SLOTS {
            if let Some(value) = slots.get(slot) {
                core.set(slot, value);
            }
        }
        let new = crate::format::insert_missing(fmt, &old, &core);
        let Some(out) = crate::format::apply_write(&old, &new) else {
            return Ok(Vec::new());
        };
        std::fs::write(path, out)?;
        let before = crate::format::read(fmt, &old);
        let after = crate::format::read(fmt, &new);
        Ok(crate::library::SLOTS
            .iter()
            .filter(|slot| !before.contains_key(**slot) && after.contains_key(**slot))
            .map(|slot| slot.to_string())
            .collect())
    }
    /// Whether any of `fmt`'s rules match `line` (overrides delete).
    fn color_line(fmt: &str, line: &str) -> bool;
}

/// Report `fmt` is unknown, mirroring Python's `KeyError`-adjacent usage
/// errors (`unknown format 'x' (choose from ...)`).
pub fn unknown_format(name: &str, names: &[&str]) -> String {
    format!(
        "unknown format '{}' (choose from {})",
        name,
        names.join(", ")
    )
}
