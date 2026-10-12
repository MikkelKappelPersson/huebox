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
