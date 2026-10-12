//! Bordered panels + top-row buttons: the compositor chrome (§8.1, §15.7).
//!
//! Rust port of the `Panel` / `ThemesButton` / `ImportButton` /
//! `ImportFooterButton` half of `huebox/app.py` (spec §4.3.2 buttons, §8
//! chrome, §15.7 panel limits). Thin by rule: this module owns only
//! chrome — border titles, env toggles, min/max clamps and button paint.
//! Content rows arrive already built (frame or side/top row builders);
//! panels compose them, never re-render cells.
//!
//! Every Python `Panel(...)` mount has a bordered block here in the same
//! layout position with the same title: `THEME` (stacked controls + side
//! left), `EXAMPLES` (stacked examples + side right), `EDITOR`
//! (compositor top). Border ink is `palette-8`, the title bold
//! foreground on background — Ratatui `Block` with round borders is the
//! native equivalent of Textual's `border: round $border`.
//! * Border cells are chrome: clicks on them select nothing (the shell
//!   resolves clicks through row-announced hits plus button rects only,
//!   so a border/title/pad cell answers `None` and holds still).
//! * `Themes` / `Import` are click targets in the top info column in the
//!   same position Python mounts them (under the theme subject), routed
//!   through the same key surface as `t` / `i` — never direct state
//!   edits. Import dialog footer buttons (`Import` / `Cancel`) are
//!   click-only mirrors of the dialog's `Enter` / `Esc` bindings.
//! * Env toggles, exact Python semantics (default on, `=0` opts out):
//!   `HUEBOX_PANELS` (frameless stack), `HUEBOX_PANEL_LIMITS`
//!   (unbounded layout), `HUEBOX_PANEL_PAD` (touching borders),
//!   `HUEBOX_EDITOR_PANEL` (bare stack top, no buttons),
//!   `HUEBOX_TOP` (bare stack top, same opt-out the bare frame keeps).

use huebox_core::Slots;
use huebox_render::{slot_hex, Cell, Row};
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders};

use crate::cells::hex_color;

/// Stacked/side controls panel title (`PANEL_TITLES["controls"]`).
pub const CONTROLS_TITLE: &str = "THEME";
/// Stacked/side live panel title (`PANEL_TITLES["examples"]`).
pub const EXAMPLES_TITLE: &str = "EXAMPLES";
/// Compositor-top editor panel title (`EDITOR_TITLE`).
pub const EDITOR_TITLE: &str = "EDITOR";

/// Panel bounds floor: borders around the 40-column draw floor.
pub const PANEL_MIN_W: usize = 42;
/// Past this extra columns are background fill, not content.
pub const PANEL_MAX_W: usize = 160;
/// Borders around a single row.
pub const PANEL_MIN_H: usize = 3;
/// Past this extra rows are background fill, not content.
pub const PANEL_MAX_H: usize = 50;

/// Panel size bounds: `(min_w, max_w, min_h, max_h)` per panel (§15.7).
#[allow(clippy::type_complexity)]
pub const PANEL_LIMITS: [(&str, (usize, usize, usize, usize)); 4] = [
    ("controls", (42, 160, 5, 50)),
    ("examples", (42, 160, 4, 50)),
    ("editor", (30, 120, 5, 12)),
    ("top", (42, 160, 4, 12)),
];

/// Horizontal air inside every bordered panel (decision 45).
pub const PANEL_PAD: usize = 1;
/// A padded panel needs the draw floor plus its own air.
pub const PANEL_PAD_MIN_W: usize = 44;

/// Whether the panel layout is on (`HUEBOX_PANELS`, default on).
pub fn panels_enabled() -> bool {
    std::env::var("HUEBOX_PANELS").as_deref() != Ok("0")
}

/// Whether panel min/max bounds apply (`HUEBOX_PANEL_LIMITS`, default on).
pub fn panel_limits_enabled() -> bool {
    std::env::var("HUEBOX_PANEL_LIMITS").as_deref() != Ok("0")
}

/// Whether panels pad their content (`HUEBOX_PANEL_PAD`, default on).
pub fn panel_pad_enabled() -> bool {
    std::env::var("HUEBOX_PANEL_PAD").as_deref() != Ok("0")
}

/// Whether the compositor top is laid out (`HUEBOX_EDITOR_PANEL`).
pub fn editor_panel_enabled() -> bool {
    std::env::var("HUEBOX_EDITOR_PANEL").as_deref() != Ok("0")
}

/// Whether the unified top may stand (`HUEBOX_TOP`, default on).
pub fn top_enabled() -> bool {
    std::env::var("HUEBOX_TOP").as_deref() != Ok("0")
}

/// Clamp a panel's `(width, height)` to its `PANEL_LIMITS` entry.
///
/// Pure: the same bounds the compositor enforces, as numbers a headless
/// test asserts without a backend. Unknown names take the generic bounds.
pub fn clamp_panel(name: &str, width: usize, height: usize) -> (usize, usize) {
    let (min_w, max_w, min_h, max_h) = PANEL_LIMITS
        .iter()
        .find(|(key, _)| *key == name)
        .map(|(_, limits)| *limits)
        .unwrap_or((PANEL_MIN_W, PANEL_MAX_W, PANEL_MIN_H, PANEL_MAX_H));
    (width.clamp(min_w, max_w), height.clamp(min_h, max_h))
}

/// The size the frame is laid out at: `min(size, max)` (§15.7).
///
/// Within the maxima this is identity; past them content stops reflowing
/// and the extra screen stays fill. Disabled with the panels' own
/// opt-out so `HUEBOX_PANEL_LIMITS=0` restores the unbounded layout.
pub fn layout_size(width: usize, height: usize) -> (usize, usize) {
    if !panel_limits_enabled() {
        return (width, height);
    }
    (width.min(PANEL_MAX_W), height.min(PANEL_MAX_H))
}

/// One bordered group of the frame's blocks: border + title only.
///
/// `Block` with round borders, border ink `palette-8`, title bold
/// foreground on background — the Ratatui spelling of Textual's
/// `border: round $border` + `border-title-style: bold`. The title wears
/// Textual's `─ TITLE ` shape (dash-air before, air after) so the top
/// border reads `╭─ TITLE ─` on both. Never focusable; a click on the
/// border is chrome and selects nothing (§4.3.2).
pub fn panel_block(title: &str, slots: &Slots) -> Block<'static> {
    let bg = hex_color(slot_hex(slots, "background"));
    let border = hex_color(slot_hex(slots, "palette-8"));
    let fg = hex_color(slot_hex(slots, "foreground"));
    // Two runs: the leading dash wears the BORDER ink (it is border,
    // not text), the ` TITLE ` that follows wears bold foreground.
    let heading = Line::from(vec![
        Span::styled("─", Style::new().fg(border).bg(bg)),
        Span::styled(
            format!(" {title} "),
            Style::new().fg(fg).bg(bg).add_modifier(Modifier::BOLD),
        ),
    ]);
    Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::new().fg(border).bg(bg))
        .style(Style::new().bg(bg))
        .title(heading)
}

/// Selection-pair ink both top buttons wear (theme-closed, like Python).
fn button_style(slots: &Slots) -> Style {
    let bg = hex_color(slot_hex(slots, "selection-background"));
    let fg = hex_color(slot_hex(slots, "selection-foreground"));
    Style::new().fg(fg).bg(bg).add_modifier(Modifier::BOLD)
}

/// One flat top button row, centered in `width`.
///
/// Never keeps focus (the shell never routes arrows into it); a drag
/// across it never begins a text selection. Painted only in the theme's
/// own selection pair so it reads as part of the theme. Narrow columns
/// clip the label rather than vetoing the top (Python squeezes the
/// button with its share the same way).
pub fn button_line(label: &str, width: usize, slots: &Slots) -> Line<'static> {
    let style = button_style(slots);
    let fill = hex_color(slot_hex(slots, "background"));
    // Squeezed shares clip, like the Python button squeezing with its
    // column rather than overflowing it.
    let mut label: String = label.chars().take(width.max(1)).collect();
    if label.is_empty() {
        label.push(' ');
    }
    let label_w = label.chars().count();
    let pad = width.saturating_sub(label_w) / 2;
    let mut spans = vec![];
    for _ in 0..pad {
        spans.push(Span::styled(" ", Style::new().bg(fill)));
    }
    spans.push(Span::styled(label.to_owned(), style));
    while spans
        .iter()
        .map(|s: &Span| s.content.chars().count())
        .sum::<usize>()
        < width
    {
        spans.push(Span::styled(" ", Style::new().bg(fill)));
    }
    let mut line = Line::from(spans);
    line.style = Style::new().bg(fill);
    line
}

/// The top info column's `Themes` button row (mouse mirror of `t`).
pub fn themes_button_line(width: usize, slots: &Slots) -> Line<'static> {
    button_line("Themes", width.max(6), slots)
}

/// The top info column's `Import` button row (mouse mirror of `i`).
pub fn import_button_line(width: usize, slots: &Slots) -> Line<'static> {
    button_line("Import", width.max(6), slots)
}

/// The import dialog's footer button row: `Import` + `Cancel`.
///
/// Click-only mirrors of the dialog's `Enter` / `Esc` bindings (never
/// focusable, so tab-trapping is impossible). Returns the line plus the
/// content-column ranges of each button for the click map.
pub fn import_footer_buttons(
    width: usize,
    slots: &Slots,
) -> (Line<'static>, (usize, usize), (usize, usize)) {
    let style = button_style(slots);
    let fill = hex_color(slot_hex(slots, "background"));
    // `  [ Import ]    [ Cancel ]  ` centered in the dialog width.
    let gap = "    ";
    let left = "[ Import ]";
    let right = "[ Cancel ]";
    let total = left.chars().count() + gap.chars().count() + right.chars().count();
    let pad = width.saturating_sub(total) / 2;
    let mut text = String::new();
    for _ in 0..pad {
        text.push(' ');
    }
    let confirm_x0 = text.chars().count();
    text.push_str(left);
    let confirm_x1 = text.chars().count();
    text.push_str(gap);
    let cancel_x0 = text.chars().count();
    text.push_str(right);
    let cancel_x1 = text.chars().count();
    while text.chars().count() < width {
        text.push(' ');
    }
    // One line, three runs: air, buttons, air — so the spans carry the
    // click ranges without a second layout pass.
    let mut spans = vec![];
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let in_button =
            (confirm_x0..confirm_x1).contains(&i) || (cancel_x0..cancel_x1).contains(&i);
        let start = i;
        while i < chars.len()
            && ((confirm_x0..confirm_x1).contains(&i) || (cancel_x0..cancel_x1).contains(&i))
                == in_button
        {
            i += 1;
        }
        let chunk: String = chars[start..i].iter().collect();
        spans.push(Span::styled(
            chunk,
            if in_button {
                style
            } else {
                Style::new().bg(fill)
            },
        ));
    }
    let mut line = Line::from(spans);
    line.style = Style::new().bg(fill);
    (line, (confirm_x0, confirm_x1), (cancel_x0, cancel_x1))
}

/// Live blocks ride in an open collapsible rather than as bare rows:
/// the header stands in for the frame's title row, so open or shut the
/// rows below never shift by more than what the block hid. Compositor
/// chrome around `draw_editor` rows, never re-rendered content.
pub const LIVE_BLOCKS: [&str; 3] = ["examples", "diff", "sample"];

/// What the compositor calls a live block (`LIVE_TITLES`).
pub fn live_title(name: &str) -> Option<&'static str> {
    match name {
        "examples" => Some("Interface text"),
        "diff" => Some("Live diff"),
        "sample" => Some("Live code"),
        _ => None,
    }
}

/// Whether the live collapsibles are on (`HUEBOX_COLLAPSIBLE`, default
/// on). `=0` opts out back to the bare rows — what the headless harness
/// asserts, never product.
pub fn collapsible_enabled() -> bool {
    std::env::var("HUEBOX_COLLAPSIBLE").as_deref() != Ok("0")
}

/// Collapse toggles: shifted, because the bare letters colour. All three
/// are free in `apply_key`, so the toggles never steal a colour key.
pub const COLLAPSE_KEYS: [(&str, &str); 3] = [("E", "examples"), ("D", "diff"), ("C", "sample")];

/// The live block `key` toggles, if it is a collapse key.
pub fn collapse_target(key: &str) -> Option<&'static str> {
    COLLAPSE_KEYS
        .iter()
        .find(|(k, _)| *k == key)
        .map(|(_, block)| *block)
}

/// The header row standing in for a live block's title row: `▼ {title}`
/// open, `▶ {title}` shut — bold foreground on background, the
/// `CollapsibleTitle` CSS verbatim. Unbackdropped: the caller lays it
/// with the block's own rows.
pub fn live_header_row(name: &str, collapsed: bool, slots: &Slots) -> Row {
    let marker = if collapsed { '▶' } else { '▼' };
    let title = live_title(name).unwrap_or(name);
    let fg = slot_hex(slots, "foreground").to_owned();
    let bg = slot_hex(slots, "background").to_owned();
    let mut row = vec![
        Cell::new(marker, &fg, &bg).bolded(),
        Cell::new(' ', &fg, &bg).bolded(),
    ];
    for ch in title.chars() {
        row.push(Cell::new(ch, &fg, &bg).bolded());
    }
    row
}

#[cfg(test)]
mod tests {
    use super::*;

    fn slots() -> Slots {
        let mut slots = Slots::default();
        slots.set("background", "#101014");
        slots.set("foreground", "#e6e6ea");
        slots.set("palette-8", "#808080");
        slots.set("selection-background", "#2a2a34");
        slots.set("selection-foreground", "#f0f0f8");
        slots
    }

    #[test]
    fn clamp_holds_named_bounds() {
        assert_eq!(clamp_panel("controls", 10, 100), (42, 50));
        assert_eq!(clamp_panel("controls", 200, 100), (160, 50));
        assert_eq!(clamp_panel("editor", 10, 2), (30, 5));
        assert_eq!(clamp_panel("editor", 200, 200), (120, 12));
        assert_eq!(clamp_panel("unknown", 10, 100), (42, 50));
        assert_eq!(clamp_panel("controls", 100, 30), (100, 30));
    }

    #[test]
    fn titles_match_python() {
        assert_eq!(CONTROLS_TITLE, "THEME");
        assert_eq!(EXAMPLES_TITLE, "EXAMPLES");
        assert_eq!(EDITOR_TITLE, "EDITOR");
    }

    #[test]
    fn buttons_wear_selection_pair_bold() {
        let slots = slots();
        let line = themes_button_line(20, &slots);
        let text: String = line.spans.iter().map(|s| s.content.as_ref()).collect();
        assert!(text.contains("Themes"), "{text:?}");
        let btn = line
            .spans
            .iter()
            .find(|s| s.content.contains("Themes"))
            .expect("button span");
        assert!(btn.style.add_modifier.contains(Modifier::BOLD));
        let import = import_button_line(20, &slots);
        let text: String = import.spans.iter().map(|s| s.content.as_ref()).collect();
        assert!(text.contains("Import"), "{text:?}");
    }

    #[test]
    fn footer_buttons_announce_ranges() {
        let slots = slots();
        let (line, confirm, cancel) = import_footer_buttons(40, &slots);
        let text: String = line.spans.iter().map(|s| s.content.as_ref()).collect();
        assert_eq!(text.chars().count(), 40);
        assert!(text.contains("[ Import ]"), "{text:?}");
        assert!(text.contains("[ Cancel ]"), "{text:?}");
        let slice: String = text
            .chars()
            .skip(confirm.0)
            .take(confirm.1 - confirm.0)
            .collect();
        assert_eq!(slice, "[ Import ]");
        let slice: String = text
            .chars()
            .skip(cancel.0)
            .take(cancel.1 - cancel.0)
            .collect();
        assert_eq!(slice, "[ Cancel ]");
        assert!(confirm.1 <= cancel.0);
    }
}
