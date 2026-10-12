//! huebox: terminal theme editor with live preview.
//!
//! One crate, seven modules — each owns one job, and the dependency rule
//! is absolute: `core` stands alone; `format` and `render` build on `core`
//! only; `library` on `core` + `format`; `editor` on `core` + `render`;
//! `app` on `core` + `render` + `editor`; `cli` on everything. No cycles.
//!
//! - [`core`]: 22-slot model, hex/rgb/hsv maths, luminance, step sizes.
//! - [`format`]: terminal colour-format rules (ghostty, kitty, alacritty).
//! - [`library`]: providers, `detect`, `themes` — the facades callers use.
//! - [`render`]: pure frame rows — no compositor, no I/O.
//! - [`editor`]: the editing session (`EditorState` + `apply_key`).
//! - [`app`]: the Ratatui shell — keys in, redraw, popups, prompts.
//! - [`cli`]: clap surface, dispatch, composition root.

#![forbid(unsafe_code)]

pub mod app;
pub mod cli;
pub mod core;
pub mod editor;
pub mod format;
pub mod library;
pub mod render;
