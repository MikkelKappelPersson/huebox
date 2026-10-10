//! The bare frame: `draw_editor` as pure rows + hits (§15).
//!
//! Rust port of `draw_editor` in `huebox/editor.py` (the bare-frame half;
//! popups and the state machine are later phases). Where the Python
//! writes styled text to stdout, [`frame`] returns cells: `(rows, hits,
//! regions)` — the downstream app maps rows to Ratatui spans and clicks
//! through `hits` via [`slot_at`], exactly as `app.py` does through
//! `frame_hits`.

use huebox_core::Slots;

use crate::banner::{banner_lines, mini_banner_lines};
use crate::chrome::{CHROME_MUTED, chrome, hint_line, title, wordmark_default};
use crate::diff::diff_lines;
use crate::examples::example_lines;
use crate::floor::{backdrop, too_small_frame};
use crate::grid::{
    TOP_MIN_COLS, grid_geometry, pad_left, top_right_rows,
};
use crate::hsv::{hsv_numbers, hsv_readout};
use crate::layout::{MIN_COLS, MIN_ROWS, clip_row, row_visible};
use crate::palette::{CELL_FULL, PALETTE, named_cell_width, palette_rows};
use crate::sample::sample_lines;
use crate::{Row, painted, plain, slot_hex};

/// `0-7 base   8-15 bright` — a courtesy (the grid is numbered), so the
/// first decoration the frame spends.
pub const PALETTE_LEGEND: &str = "  0-7 base   8-15 bright";
/// The strip whole: header + three rows.
pub const EXAMPLES_ROWS: usize = 4;
/// Header + one row; below this it goes.
pub const EXAMPLES_FLOOR: usize = 2;
/// The diff whole: header + hunk + two pairs.
pub const DIFF_ROWS: usize = 6;
/// Header + hunk + one removed/added pair.
pub const DIFF_FLOOR: usize = 4;
/// The code block floor: header + three lines.
pub const SAMPLE_FLOOR: usize = 4;
/// What the frame keeps free for the widgets before touching one: the
/// strip whole, the sample at its floor, and a row of air under it.
pub const WIDGET_BUDGET: usize = EXAMPLES_ROWS + SAMPLE_FLOOR + 1;

/// One clickable cell: the frame row `y`, columns `x0`..=`x1`, and the slot.
///
/// Column-inclusive at both ends, because a cell that answered to every
/// column but its last would leave a sliver no one can hit.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Hit {
    /// Frame row.
    pub y: usize,
    /// First clickable column.
    pub x0: usize,
    /// Last clickable column, inclusive.
    pub x1: usize,
    /// Slot index into the 22 canonical slots.
    pub slot: usize,
}

/// The slot a click at (`x`, `y`) lands on, or `None` for the chrome.
pub fn slot_at(hits: &[Hit], x: usize, y: usize) -> Option<usize> {
    hits.iter().find(|h| h.y == y && h.x0 <= x && x <= h.x1).map(|h| h.slot)
}

/// The editor footer's `(key, what)` pairs — one source, two paints.
///
/// The frame folds these into tail rows (headless) and the compositor
/// shows the same pairs in its docked bar. The step multiplier stays out:
/// `f` still cycles it and the status line confirms the step.
pub fn editor_hints(undo_len: usize) -> Vec<(String, String)> {
    vec![
        ("arrows".to_owned(), "move".to_owned()),
        ("w/e".to_owned(), "hue".to_owned()),
        ("s/d".to_owned(), "sat".to_owned()),
        ("x/c".to_owned(), "val".to_owned()),
        ("^S".to_owned(), "save".to_owned()),
        ("^A".to_owned(), "apply".to_owned()),
        ("N".to_owned(), "save as new".to_owned()),
        ("u".to_owned(), format!("undo({undo_len})")),
        ("r".to_owned(), "revert".to_owned()),
        ("t".to_owned(), "themes".to_owned()),
        ("i".to_owned(), "import".to_owned()),
    ]
}

/// What [`frame`] needs: the subject, the size, and the frame switches.
pub struct FrameOpts {
    /// Theme label (`fmt` when no `head` overrides it).
    pub label: String,
    /// The file a save writes (shown when it fits).
    pub path: String,
    /// Selected slot index.
    pub sel: usize,
    /// Undo depth (names the footer's `undo(n)`).
    pub undo_len: usize,
    /// Status line (last tail row when non-empty).
    pub status: String,
    /// Frame width in columns.
    pub cols: usize,
    /// Frame height in rows.
    pub rows: usize,
    /// `Some(true)` forces the raster banner, `Some(false)` keeps the
    /// wordmark, `None` walks the ladder.
    pub use_banner: Option<bool>,
    /// Content rows' own air: the bare frame's two columns, `""` inside
    /// bordered panels.
    pub indent: String,
    /// Keep the hints tail plus the status row (the compositor passes
    /// `false`: hints live in its docked bar instead).
    pub footer: bool,
}

/// What [`frame`] returns: laid-out rows plus the click map.
pub struct FrameOut {
    /// Backdrop'd rows, at most `opts.rows` of them.
    pub rows: Vec<Row>,
    /// Clickable colour cells, announced as painted.
    pub hits: Vec<Hit>,
    /// `(name, first, count)` checkpoints for the blocks that survived.
    pub regions: Vec<(String, usize, usize)>,
}

/// The frame as pure rows: body, widgets, tail on the buffer background.
///
/// Ports `draw_editor` cell-for-cell: the top block (side-by-side above
/// [`TOP_MIN_COLS`], stacked below), palette + interface grids from the
/// shared cell family, the selected readout with its HSV ladder, the
/// examples/diff/sample widgets sharing the leftover rows down to their
/// floors, the footer tail, and the header logo drawn out of leftover
/// rows. Below [`MIN_COLS`]×[`MIN_ROWS`] the frame is the too-small hint
/// and nothing else.
pub fn frame(opts: &FrameOpts, slots: &Slots) -> FrameOut {
    let cols = opts.cols;
    let rows = opts.rows;
    let indent = opts.indent.as_str();
    if cols < MIN_COLS || rows < MIN_ROWS {
        return FrameOut {
            rows: vec![too_small_frame(cols)],
            hits: vec![],
            regions: vec![],
        };
    }
    let grid = grid_geometry(cols, indent);
    let sel = opts.sel;

    // Built before the first body row: the banner insertion needs the
    // whole frame — body, widgets and tail — before it can know whether
    // the rows below leave room.
    let mut tail: Vec<Row> = if opts.footer {
        hint_line(
            slots,
            &editor_hints(opts.undo_len)
                .iter()
                .map(|(k, w)| (k.as_str(), w.as_str()))
                .collect::<Vec<_>>(),
            cols.saturating_sub(2),
        )
        .into_iter()
        .map(|mut line| {
            let mut row = plain("  ");
            row.append(&mut line);
            row
        })
        .collect()
    } else {
        vec![]
    };
    if !opts.status.is_empty() && opts.footer {
        let mut row = plain("  ");
        row.extend(plain(&opts.status));
        tail.push(row);
    }

    let mut body: Vec<Row> = Vec::new();
    let mut marks: Vec<(String, usize)> = Vec::new();
    let mut hits: Vec<Hit> = Vec::new();

    let use_top = cols >= TOP_MIN_COLS && std::env::var("HUEBOX_TOP").as_deref() != Ok("0");
    let mut rights: [Row; 3] = [vec![], vec![], vec![]];
    if use_top {
        let [r0, r1, r2] = top_right_rows(&opts.label, &opts.path, slots, sel, cols);
        rights = [r0, r1, r2];
        marks.push(("header".to_owned(), body.len()));
        let mut row = pad_left({
            let mut r = plain("  ");
            r.extend(wordmark_default(slots));
            r
        });
        row.extend(rights[0].clone());
        body.push(clip_row(&row, cols));
        marks.push(("selected".to_owned(), body.len()));
        let mut row = pad_left(vec![]);
        row.extend(rights[1].clone());
        body.push(clip_row(&row, cols));
        let mut row = pad_left(vec![]);
        row.extend(rights[2].clone());
        body.push(clip_row(&row, cols));
        body.push(vec![]);
    } else {
        let mut first = plain("  ");
        first.extend(wordmark_default(slots));
        first.extend(plain("  "));
        first.extend(chrome(&opts.label, "foreground", slots));
        if !opts.path.is_empty()
            && "  huebox  ".len() + opts.label.chars().count() + 2 + opts.path.chars().count()
                <= cols
        {
            first.extend(plain("  "));
            first.extend(chrome(&opts.path, CHROME_MUTED, slots));
        }
        marks.push(("header".to_owned(), body.len()));
        body.push(first);
        body.push(vec![]);
    }

    let (per_row, show_hex) = (grid.palette_cols, grid.show_hex);
    let cellw = if show_hex { CELL_FULL } else { crate::palette::CELL_MIN };

    marks.push(("palette".to_owned(), body.len()));
    {
        let mut row = plain(indent);
        row.extend(title("Palette", slots, ""));
        body.push(row);
    }
    let sel_opt = Some(sel);
    for (number, cell_row) in palette_rows(slots, sel_opt, per_row, show_hex, None)
        .into_iter()
        .enumerate()
    {
        let y = body.len();
        let mut row = plain(indent);
        row.extend(cell_row);
        body.push(row);
        let start = number * per_row;
        for (column, index) in (start..start + per_row).take(PALETTE.len().saturating_sub(start)).enumerate() {
            let x0 = indent.chars().count() + column * cellw;
            hits.push(Hit { y, x0, x1: x0 + cellw - 1, slot: index });
        }
    }
    let legend = chrome(PALETTE_LEGEND, CHROME_MUTED, slots);
    body.push(legend.clone());
    body.push(vec![]);

    marks.push(("interface".to_owned(), body.len()));
    let per = grid.named_cols;
    let ncellw = named_cell_width(grid.named_abbrev, grid.named_show_hex);
    {
        let mut row = plain(indent);
        row.extend(title("Interface", slots, ""));
        body.push(row);
    }
    for (number, cell_row) in crate::palette::interface_rows(
        slots,
        sel_opt,
        per,
        grid.named_abbrev,
        grid.named_show_hex,
    )
    .into_iter()
    .enumerate()
    {
        let y = body.len();
        let mut row = plain(indent);
        row.extend(cell_row);
        let start = number * per;
        body.push(row);
        for column in 0..crate::palette::NAMED.len().saturating_sub(start).min(per) {
            let x0 = indent.chars().count() + column * (ncellw + 2);
            hits.push(Hit {
                y,
                x0,
                x1: x0 + ncellw - 1,
                slot: PALETTE.len() + start + column,
            });
        }
    }
    body.push(vec![]);

    if !use_top {
        marks.push(("selected".to_owned(), body.len()));
        let key = crate::palette::SLOTS[sel.min(crate::palette::SLOTS.len() - 1)];
        let value = slot_hex(slots, key);
        let subject = crate::grid::selected_core(slots, key, value, indent);
        let (r, g, b) = huebox_core::hex_to_rgb(value);
        let (h, s, v) = huebox_core::rgb_to_hsv(r, g, b);
        let exact = hsv_numbers(h, s, v);
        let mut specimen = plain(&format!("{indent}{indent}"));
        specimen.extend(painted("AaBbCc 0123 ", value, ""));
        let bars = hsv_readout(slots, value, cols.saturating_sub(row_visible(&subject) + 3));
        let mut line = subject;
        if !bars.is_empty() {
            line.extend(plain("   "));
            line.extend(bars);
        }
        body.push(line);
        let mut line = specimen;
        if exact.chars().count() + 3 <= cols.saturating_sub(row_visible(&line)) {
            line.extend(plain("   "));
            line.extend(chrome(&exact, CHROME_MUTED, slots));
        }
        body.push(line);
        body.push(vec![]);
    }

    // The frame spends its decoration before it spends a widget.
    let mut spare: isize = rows as isize - body.len() as isize - tail.len() as isize;
    while spare < WIDGET_BUDGET as isize {
        // the legend is a courtesy (the grid is numbered), so the blanks
        // are the decoration proper — nearest the widgets first, leaving
        // the air at the top of the frame
        let dropped = body
            .iter()
            .rposition(|line| *line == legend)
            .or_else(|| body.iter().rposition(|line| line.is_empty()));
        let Some(dropped) = dropped else { break };
        body.remove(dropped);
        for hit in &mut hits {
            if hit.y > dropped {
                hit.y -= 1;
            }
        }
        for mark in &mut marks {
            if mark.1 > dropped {
                mark.1 -= 1;
            }
        }
        spare += 1;
    }

    let mut extra: Vec<Row> = Vec::new();
    let room_left = |extra: &[Row]| spare - extra.len() as isize;

    let examples = (EXAMPLES_ROWS as isize).min(room_left(&extra) - SAMPLE_FLOOR as isize);
    if examples >= EXAMPLES_FLOOR as isize {
        marks.push(("examples".to_owned(), body.len() + extra.len()));
        let mut row = plain(indent);
        row.extend(title("examples", slots, "(live buffer: background / selection / cursor)"));
        extra.push(row);
        extra.extend(
            example_lines(slots, Some(cols.saturating_sub(indent.chars().count())), indent)
                .into_iter()
                .take((examples as usize).saturating_sub(1)),
        );
    }
    let mut diff: Vec<Row> = Vec::new();
    if room_left(&extra) >= 2 {
        // NOTE: no pop of a trailing blank — the Python `code.pop()`
        // never fires (`"\x1b[0m".strip()` is truthy), so the lex's
        // trailing newline stays a blank row the frame lays out.
        let mut code = sample_lines(slots);
        // a truncated block drops its least informative lines rather than
        // stopping mid-program: the leading comment, the closing brace,
        // and the blank inside it
        if room_left(&extra) - 1 < code.len() as isize {
            let n = code.len();
            code = code
                .into_iter()
                .skip(1)
                .take(n.saturating_sub(2))
                .filter(|line| line.iter().any(|c| !c.ch.is_whitespace()))
                .collect();
        }
        let take = (code.len() as isize).min((room_left(&extra) - 1).max(1)) as usize;
        if take > 0 {
            let spare_rows = room_left(&extra) - 1 - take as isize;
            let mut rows_left = (DIFF_ROWS as isize - 1).min((spare_rows - 1).max(0));
            rows_left -= (rows_left - 1).rem_euclid(2);
            let lead = if spare_rows - rows_left > 2 { 1 } else { 0 };
            if rows_left >= DIFF_FLOOR as isize - 1 {
                marks.push(("diff".to_owned(), body.len() + extra.len() + lead as usize));
                if lead == 1 {
                    extra.push(vec![]);
                }
                let mut head = plain(indent);
                head.extend(title("live diff", slots, "(git-style: + added, - removed)"));
                diff.push(head);
                diff.extend(
                    diff_lines(
                        slots,
                        Some(cols.saturating_sub(indent.chars().count())),
                        &format!("{indent}{indent}"),
                    )
                    .into_iter()
                    .take(rows_left.max(0) as usize),
                );
                if spare_rows - lead - rows_left > 1 {
                    diff.push(vec![]);
                }
            }
            extra.extend(diff);
            marks.push(("sample".to_owned(), body.len() + extra.len()));
            let mut head = plain(indent);
            head.extend(title("live code", slots, "(truecolor, no reload needed)"));
            extra.push(head);
            extra.extend(code.into_iter().take(take).map(|line| {
                let mut row = plain(&format!("{indent}{indent}"));
                row.extend(line);
                row
            }));
            if room_left(&extra) - take as isize >= 2 {
                extra.push(vec![]);
            }
        }
    }

    if opts.footer {
        marks.push(("hints".to_owned(), body.len() + extra.len()));
    }
    if !opts.status.is_empty() && opts.footer {
        marks.push(("status".to_owned(), body.len() + extra.len() + tail.len() - 1));
    }

    // The header draws only out of leftover: the raster banner where it
    // fits, else the mini banner, else the wordmark the frame was built
    // with. Every mark and hit below shifts down by the logo's rows.
    spare = rows as isize - body.len() as isize - extra.len() as isize - tail.len() as isize;
    let art = banner_lines(slots, Some(cols), None);
    let logo: Vec<Row> = if !art.is_empty()
        && (opts.use_banner == Some(true)
            || (opts.use_banner.is_none() && spare >= art.len() as isize))
    {
        art
    } else {
        let mark = mini_banner_lines(slots, Some(cols));
        if !mark.is_empty() && opts.use_banner.is_none() && spare >= mark.len() as isize {
            mark
        } else {
            vec![]
        }
    };
    if !logo.is_empty() {
        let shift = logo.len();
        if use_top {
            // The banner above is the only huebox on screen now: the top
            // block's wordmark stands back to air, and the right column
            // stays where it was, one banner below.
            let palette_row = marks.iter().find(|(name, _)| name == "palette").map(|(_, r)| *r).unwrap_or(0);
            let mut blanked: Vec<Row> = (0..palette_row.min(3))
                .map(|i| {
                    let mut row = pad_left(vec![]);
                    row.extend(rights[i].clone());
                    clip_row(&row, cols)
                })
                .collect();
            blanked.extend(std::iter::repeat_with(Vec::new).take(palette_row.saturating_sub(3)));
            body.splice(0..palette_row, logo.into_iter().chain(blanked));
        } else {
            let mut subject = plain("  ");
            subject.extend(chrome(&opts.label, "foreground", slots));
            if !opts.path.is_empty()
                && "  ".len() + opts.label.chars().count() + 2 + opts.path.chars().count() <= cols
            {
                subject.extend(plain("  "));
                subject.extend(chrome(&opts.path, CHROME_MUTED, slots));
            }
            body.splice(0..2, {
                let mut ins = logo;
                ins.push(subject);
                ins.push(vec![]);
                ins
            });
        }
        for mark in &mut marks {
            if mark.0 != "header" {
                mark.1 += shift;
            }
        }
        for hit in &mut hits {
            hit.y += shift;
        }
    }

    let mut out: Vec<Row> = body.into_iter().chain(extra).chain(tail.clone()).collect();
    if out.len() > rows {
        let keep = rows.saturating_sub(tail.len());
        out = out.into_iter().take(keep).chain(tail.clone()).collect();
    }

    // resolve regions: a checkpoint past the last row is a block the
    // frame cut off — dropped, since it is also every later block's end
    let mut regions = Vec::new();
    {
        let live: Vec<(String, usize)> =
            marks.into_iter().filter(|(_, r)| *r < out.len()).collect();
        let mut edges: Vec<usize> = live.iter().map(|(_, r)| *r).collect();
        edges.push(out.len());
        for ((name, row), stop) in live.into_iter().zip(edges[1..].iter()) {
            regions.push((name, row, stop - row));
        }
    }
    // both trims happened after the cells announced themselves: off go
    // the hits on rows the frame never painted
    let kept = out.len().saturating_sub(tail.len());
    hits.retain(|hit| hit.y < kept);

    // §8.2 — every row stands on the buffer's own background
    let painted_rows: Vec<Row> =
        out.into_iter().map(|line| backdrop(&line, slots, cols)).collect();
    FrameOut { rows: painted_rows, hits, regions }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn slots() -> Slots {
        let mut s = Slots::default();
        for (i, h) in [
            "#1a1a20", "#c05c5c", "#5cc05c", "#c0a05c", "#5c8cc0", "#c05c8c", "#5cc0c0",
            "#80808c", "#d0d0d8", "#e06c6c", "#6cc06c", "#e0c06c", "#6c9ce0", "#e06c9c",
            "#6cc0c0", "#f0f0f4",
        ]
        .iter()
        .enumerate()
        {
            s.set(&format!("palette-{i}"), h);
        }
        for (slot, hex) in [
            ("background", "#101014"),
            ("foreground", "#e6e6ea"),
            ("cursor-color", "#e6e6ea"),
            ("cursor-text", "#101014"),
            ("selection-background", "#2a2a34"),
            ("selection-foreground", "#f0f0f8"),
        ] {
            s.set(slot, hex);
        }
        s
    }

    fn opts(cols: usize, rows: usize, sel: usize) -> FrameOpts {
        FrameOpts {
            label: "ghostty".to_owned(),
            path: "/tmp/x".to_owned(),
            sel,
            undo_len: 0,
            status: String::new(),
            cols,
            rows,
            use_banner: None,
            indent: "  ".to_owned(),
            footer: true,
        }
    }

    #[test]
    fn frame_rows_reach_the_width() {
        let s = slots();
        for (cols, rows) in [(80, 24), (100, 30), (40, 12), (120, 24)] {
            let out = frame(&opts(cols, rows, 5), &s);
            assert!(!out.rows.is_empty());
            assert!(out.rows.len() <= rows);
            for row in &out.rows {
                assert_eq!(crate::layout::row_visible(row), cols, "at {cols}x{rows}");
            }
        }
    }

    #[test]
    fn too_small_is_one_hint_row() {
        let s = slots();
        for (cols, rows) in [(39, 24), (80, 11), (10, 4)] {
            let out = frame(&opts(cols, rows, 5), &s);
            assert_eq!(out.rows.len(), 1);
            assert!(out.hits.is_empty());
        }
    }

    #[test]
    fn hits_resolve_through_slot_at() {
        let s = slots();
        let out = frame(&opts(80, 24, 5), &s);
        assert!(!out.hits.is_empty());
        let hit = out.hits[0];
        assert_eq!(slot_at(&out.hits, hit.x0, hit.y), Some(hit.slot));
        assert_eq!(slot_at(&out.hits, 0, 0), None);
        // every hit lands inside its row
        for hit in &out.hits {
            assert!(hit.y < out.rows.len());
            assert!(hit.x1 < 80, "{hit:?}");
        }
    }

    #[test]
    fn footer_lists_hints_and_status_last() {
        let s = slots();
        let mut o = opts(80, 24, 5);
        o.status = "saved".to_owned();
        let out = frame(&o, &s);
        let last: String = out.rows.last().unwrap().iter().map(|c| c.ch).collect();
        assert!(last.contains("saved"));
    }

    #[test]
    fn editor_hints_name_the_surface() {
        let hints = editor_hints(2);
        assert!(hints.iter().any(|(k, w)| k == "u" && w == "undo(2)"));
        assert!(hints.iter().any(|(k, _)| k == "arrows"));
    }
}
