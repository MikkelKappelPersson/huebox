//! The three small popups as pure cell rows: themes, first-run setup,
//! Ghostty overrides warning.
//!
//! Rust port of `theme_lines` / `setup_lines` / `overrides_lines` in
//! `huebox/editor.py`. Where the Python builds ANSI strings, these build
//! [`huebox_render`] cell rows plus the row-announced hits — cells are
//! built styled, never serialised and reparsed. Same windows, same marks,
//! same footers; bold does not survive (the cell model carries no bold —
//! selection reads through `>` and text, not weight).
//!
//! The import popup's rows live in the compositor (P5): behaviour-only
//! [`crate::import`] owns its cursor/selection/confirm, and this module
//! owns nothing of its paint.

use huebox_core::Slots;
use huebox_render::chrome::{chrome, hint_line, title, wordmark_default, CHROME_MUTED};
use huebox_render::frame::Hit;
use huebox_render::layout::clip_row;
use huebox_render::{plain, Row};

/// The picker footer as `(key, what)` pairs — the frame paints the key
/// and its label separately, and `pack` never folds between a key and the
/// label it belongs to.
pub const THEME_HINTS: [(&str, &str); 5] = [
    ("arrows", "move"),
    ("Enter", "use"),
    ("n", "new from buffer"),
    ("N", "save as new"),
    ("t / Esc", "back"),
];

/// First-run setup: the two ways out of an empty library.
pub const SETUP_CHOICES: [&str; 2] = ["import", "new"];
/// Choice labels.
pub const SETUP_LABELS: [&str; 2] = ["Import themes", "Create new theme"];
/// Each choice's direct spelling.
pub const SETUP_KEYS: [&str; 2] = ["i", "n"];
/// The choice footer.
pub const SETUP_HINTS: [(&str, &str); 5] = [
    ("arrows", "move"),
    ("Enter", "choose"),
    ("i", "import"),
    ("n", "new"),
    ("Esc", "quit"),
];
/// The footer while naming the new theme.
pub const SETUP_NAME_HINTS: [(&str, &str); 3] =
    [("type", "name"), ("Enter", "save"), ("Esc", "back")];
/// The longest name the field accepts: the library's own rule (letters,
/// digits, `-` and `_`, 64 max) — the editor never imports themes, so the
/// number is pinned here and the writer still has the last word.
pub const SETUP_NAME_MAX: usize = 64;

/// Ghostty overrides: `delete` removes the shadowing lines so the theme
/// shows, `keep` leaves them. Index 1 (`keep`) is the safe default.
pub const OVERRIDES_CHOICES: [&str; 2] = ["delete", "keep"];
/// Choice labels.
pub const OVERRIDES_LABELS: [&str; 2] = ["yes, delete", "no, keep overrides"];
/// Each choice's direct spelling.
pub const OVERRIDES_KEYS: [&str; 2] = ["y", "n"];
/// The warning footer.
pub const OVERRIDES_HINTS: [(&str, &str); 4] = [
    ("arrows", "move"),
    ("Enter", "choose"),
    ("y", "delete"),
    ("n / Esc", "keep"),
];

/// One dialog's rows plus its click map.
///
/// `hits` are announced by the rows that draw them (`slot` is the library
/// row / choice index) — a second description of the window would drift
/// the moment either changed, and a click would land on the wrong row.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct DialogFrame {
    /// Clipped rows, at most the asked height.
    pub rows: Vec<Row>,
    /// Clickable rows in dialog coords.
    pub hits: Vec<Hit>,
}

fn header_row(word: &str, slots: &Slots) -> Row {
    let mut row = plain("  ");
    row.extend(wordmark_default(slots));
    row.extend(plain("  "));
    row.extend(title(word, slots, ""));
    row
}

fn footer_rows(hints: &[(&str, &str)], cols: usize, status: &str, slots: &Slots) -> Vec<Row> {
    let mut footer: Vec<Row> = hint_line(slots, hints, cols.saturating_sub(2))
        .into_iter()
        .map(|mut line| {
            let mut row = plain("  ");
            row.append(&mut line);
            row
        })
        .collect();
    if !status.is_empty() {
        let mut row = plain("  ");
        row.extend(plain(status));
        footer.push(row);
    }
    footer
}

/// Trim `out` to `rows`, keeping the footer: the status line is the last
/// row and is never the row that gets cut, exactly as in the editor frame.
fn trim_keep_footer(out: Vec<Row>, footer: &[Row], rows: usize, cols: usize) -> Vec<Row> {
    let mut out = out;
    if out.len() > rows {
        out.truncate(out.len() - footer.len());
        out.extend(footer.iter().cloned());
    }
    out.into_iter()
        .take(rows)
        .map(|row| clip_row(&row, cols))
        .collect()
}

/// The theme picker as a frame of rows.
///
/// `current` is the session's theme, marked `*` while it is one of the
/// rows; the selected row carries `>`. `footer=false` drops the hint
/// footer and the status row: the compositor shows those in the dialog's
/// bar instead, so the rows are the list alone.
#[allow(clippy::too_many_arguments)]
pub fn theme_lines(
    names: &[String],
    index: usize,
    current: &str,
    cols: usize,
    rows: usize,
    status: &str,
    slots: &Slots,
    footer: bool,
) -> DialogFrame {
    let mut out = vec![header_row("themes", slots), vec![]];
    let footer_rows = if footer {
        footer_rows(&THEME_HINTS, cols, status, slots)
    } else {
        vec![]
    };
    let mut hits = Vec::new();

    if names.is_empty() {
        let mut row = plain("  ");
        row.extend(chrome(
            "no themes yet - N makes one from this buffer",
            CHROME_MUTED,
            slots,
        ));
        out.push(row);
    } else {
        let index = index.min(names.len() - 1);
        // Two rows of the budget: the "x-y of n" counter and the blank
        // before the footer — the footer itself is never trimmed.
        let room = (rows.saturating_sub(out.len() + footer_rows.len() + 2)).max(1);
        let start = (index + 1)
            .saturating_sub(room)
            .min(names.len().saturating_sub(room));
        for (row, name) in names
            .iter()
            .enumerate()
            .skip(start)
            .take(room.min(names.len().saturating_sub(start)))
        {
            hits.push(Hit {
                y: out.len(),
                x0: 2,
                x1: cols.saturating_sub(1),
                slot: row,
            });
            let mut line = plain("  ");
            if row == index {
                line.extend(chrome(">", "foreground", slots));
            }
            if name == current {
                line.extend(chrome("*", CHROME_MUTED, slots));
            }
            line.extend(plain(" "));
            line.extend(chrome(name, "foreground", slots));
            out.push(line);
        }
        if start > 0 || names.len() > start + room {
            let mut row = plain("  ");
            row.extend(chrome(
                &format!(
                    "{}-{} of {}",
                    start + 1,
                    (start + room).min(names.len()),
                    names.len()
                ),
                CHROME_MUTED,
                slots,
            ));
            out.push(row);
        }
    }
    out.push(vec![]);
    out.extend(footer_rows.iter().cloned());
    DialogFrame {
        rows: trim_keep_footer(out, &footer_rows, rows, cols),
        hits,
    }
}

/// Every picker row as `Hit`s whose `slot` is the row in the library.
///
/// The window is the point of this one: a row's frame position depends on
/// how far down the library it is, so ask at the moment the frame is up.
pub fn theme_hits(
    names: &[String],
    index: usize,
    current: &str,
    cols: usize,
    rows: usize,
    status: &str,
    slots: &Slots,
) -> Vec<Hit> {
    theme_lines(names, index, current, cols, rows, status, slots, true).hits
}

/// One setup row: `> label (key)`, the selected choice marked.
fn setup_row(choice: usize, selected: bool, slots: &Slots) -> Row {
    let mut row = plain("  ");
    row.extend(plain(if selected { "> " } else { "  " }));
    // Name and key in the session foreground (bold does not survive the
    // cell model; the `>` carries the selection).
    let mut text = chrome(
        &format!("{}  ({})", SETUP_LABELS[choice], SETUP_KEYS[choice]),
        "foreground",
        slots,
    );
    row.append(&mut text);
    row
}

/// The first-run choice as a frame of rows: import or create.
///
/// `name = Some` is the naming mode: the choices step aside for the
/// one-line field (the typed name plus a `_` cursor) and the footer names
/// its own keys. Nothing is clickable there, so `hits` stays empty and a
/// click falls through to chrome.
pub fn setup_lines(
    index: usize,
    cols: usize,
    rows: usize,
    status: &str,
    slots: &Slots,
    name: Option<&str>,
    footer: bool,
) -> DialogFrame {
    if let Some(name) = name {
        let footer_rows = if footer {
            footer_rows(&SETUP_NAME_HINTS, cols, status, slots)
        } else {
            vec![]
        };
        let mut out = vec![
            header_row("welcome", slots),
            vec![],
            {
                let mut row = plain("  ");
                row.extend(chrome(
                    "name the new theme - Enter saves it",
                    CHROME_MUTED,
                    slots,
                ));
                row
            },
            vec![],
            {
                let mut row = plain("  > ");
                row.extend(chrome(&format!("{name}_"), "foreground", slots));
                row
            },
            vec![],
        ];
        out.extend(footer_rows.iter().cloned());
        return DialogFrame {
            rows: trim_keep_footer(out, &footer_rows, rows, cols),
            hits: vec![],
        };
    }
    let index = index.min(SETUP_CHOICES.len() - 1);
    let mut out = vec![
        header_row("welcome", slots),
        vec![],
        {
            let mut row = plain("  ");
            row.extend(chrome(
                "no themes yet - pick one to start",
                CHROME_MUTED,
                slots,
            ));
            row
        },
        vec![],
    ];
    let mut hits = Vec::new();
    for (row, _) in SETUP_CHOICES.iter().enumerate() {
        hits.push(Hit {
            y: out.len(),
            x0: 2,
            x1: cols.saturating_sub(1),
            slot: row,
        });
        out.push(setup_row(row, row == index, slots));
    }
    out.push(vec![]);
    let footer_rows = if footer {
        footer_rows(&SETUP_HINTS, cols, status, slots)
    } else {
        vec![]
    };
    out.extend(footer_rows.iter().cloned());
    DialogFrame {
        rows: trim_keep_footer(out, &footer_rows, rows, cols),
        hits,
    }
}

/// Every setup row as `Hit`s whose `slot` is the choice index.
pub fn setup_hits(index: usize, cols: usize, rows: usize, status: &str, slots: &Slots) -> Vec<Hit> {
    setup_lines(index, cols, rows, status, slots, None, true).hits
}

/// One overrides row: `> label (key)`, the selected choice marked.
fn overrides_row(choice: usize, selected: bool, slots: &Slots) -> Row {
    let mut row = plain("  ");
    row.extend(plain(if selected { "> " } else { "  " }));
    let mut text = chrome(
        &format!("{}  ({})", OVERRIDES_LABELS[choice], OVERRIDES_KEYS[choice]),
        "foreground",
        slots,
    );
    row.append(&mut text);
    row
}

/// The Ghostty overrides warning as a frame of rows.
#[allow(clippy::too_many_arguments)]
pub fn overrides_lines(
    main: &str,
    keys: &[String],
    index: usize,
    cols: usize,
    rows: usize,
    status: &str,
    slots: &Slots,
    footer: bool,
) -> DialogFrame {
    let index = index.min(OVERRIDES_CHOICES.len() - 1);
    let mut out = vec![
        header_row("delete config overrides?", slots),
        vec![],
        {
            let mut row = plain("  ");
            row.extend(chrome(
                &format!(
                    "{} colour(s) in {main} shadow the exported theme",
                    keys.len()
                ),
                CHROME_MUTED,
                slots,
            ));
            row
        },
        {
            let mut row = plain("  ");
            row.extend(chrome(&keys.join(", "), CHROME_MUTED, slots));
            if keys.is_empty() {
                row.extend(chrome("no colours", CHROME_MUTED, slots));
            }
            row
        },
        vec![],
    ];
    let mut hits = Vec::new();
    for (row, _) in OVERRIDES_CHOICES.iter().enumerate() {
        hits.push(Hit {
            y: out.len(),
            x0: 2,
            x1: cols.saturating_sub(1),
            slot: row,
        });
        out.push(overrides_row(row, row == index, slots));
    }
    out.push(vec![]);
    let footer_rows = if footer {
        footer_rows(&OVERRIDES_HINTS, cols, status, slots)
    } else {
        vec![]
    };
    out.extend(footer_rows.iter().cloned());
    DialogFrame {
        rows: trim_keep_footer(out, &footer_rows, rows, cols),
        hits,
    }
}

/// Every overrides row as `Hit`s whose `slot` is the choice index.
pub fn overrides_hits(
    main: &str,
    keys: &[String],
    index: usize,
    cols: usize,
    rows: usize,
    status: &str,
    slots: &Slots,
) -> Vec<Hit> {
    overrides_lines(main, keys, index, cols, rows, status, slots, true).hits
}
