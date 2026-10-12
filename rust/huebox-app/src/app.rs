//! The shell: one session, its cached frame, and the event loop.
//!
//! `Shell` holds the [`EditorState`](huebox_editor::EditorState) plus the
//! import popup's [`ImportState`](huebox_editor::ImportState) and renders
//! both through one `refresh` (state → cells) + `render` (cells →
//! Ratatui) split. The event loop ports research §2: block for the first
//! key, apply Press and Repeat through the router, drain the burst with
//! `poll(ZERO)`, redraw once. `ratatui::run`-style init/restore means a
//! panic cannot brick the terminal; mouse capture is enabled on entry and
//! a `Drop` guard disables it on exit — `run` does not do that part
//! automatically.
//!
//! Resize is event-driven: the compositor delivers it, the shell applies
//! it, and the frame is laid out from the live size — one size, one
//! number, never the stale one.
//!
//! Panels (spec §8.1, §15.7): where the window allows, the body is the
//! compositor top (bare logo + info column with its `Themes` / `Import`
//! buttons beside one bordered `EDITOR` panel) above bordered `THEME` /
//! `EXAMPLES` panels — stacked full-width, or side-by-side where tall
//! and wide enough. Panels compose already-built rows (the frame's own
//! slices, the side/top row builders); they never re-render cells.
//! Border/title cells are chrome: clicks on them select nothing.

use std::time::{Duration, Instant};

use huebox_core::Slots;
use huebox_editor::import::ThemeId;
use huebox_editor::{
    head_label, overrides_lines, session_path, setup_lines, theme_lines, top_subject, EditorState,
    ImportState, ImportWriter,
};
use huebox_render::floor::backdrop;
use huebox_render::frame::{self, editor_hints, FrameOpts, Hit};
use huebox_render::grid::{
    grid_geometry, side_grid, side_left_rows, side_live_rows, top_editor_meta, top_layout,
    top_left_rows, EDITOR_PAD_X, SIDE_LEFT_NARROW_ROWS, SIDE_LEFT_NARROW_W, SIDE_LEFT_ROWS,
    SIDE_LEFT_THIN_W, SIDE_LEFT_W,
};
use huebox_render::hsv::{hsv_axis, HSV_FIELD};
use huebox_render::layout::{row_visible, MIN_COLS, MIN_ROWS};
use huebox_render::palette::SLOTS;
use huebox_render::sample::pad_code_row;
use huebox_render::{slot_hex, Cell, Row};
use ratatui::crossterm::event::{self, Event, KeyEventKind, MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::Line;
use ratatui::widgets::Paragraph;
use ratatui::{DefaultTerminal, Frame};

use crate::cells::{hex_color, render_rows};
use crate::debug;
use crate::dialog::{
    apply_import_thumb, clamp_width, dialog_block, dim_buffer, import_body, import_column_height,
    import_count_text, import_cursor_y, import_preview, import_scrollbar, overrides_pairs,
    popup_rect, setup_pairs, themes_pairs, IMPORT_DIALOG_MAX_H, IMPORT_DIALOG_W, IMPORT_HINTS,
    IMPORT_LEFT_W, OVERRIDES_DIALOG_W, SETUP_DIALOG_W, THEMES_DIALOG_MAX_H, THEMES_DIALOG_W,
};
use crate::footer::{bar_line_single, bar_lines, fold_footer};
use crate::panel::{
    button_line, collapsible_enabled, editor_panel_enabled, import_footer_buttons, live_header_row,
    live_title, panel_block, panel_limits_enabled, panel_pad_enabled, panels_enabled, top_enabled,
    CONTROLS_TITLE, EDITOR_TITLE, EXAMPLES_TITLE, PANEL_MAX_H, PANEL_MAX_W, PANEL_PAD,
    PANEL_PAD_MIN_W,
};
use crate::router::{route_key, Outcome, Screen};

/// Which dialog the cached layout belongs to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PopupKind {
    /// First-run choice.
    Setup,
    /// Theme picker.
    Themes,
    /// Ghostty overrides warning.
    Overrides,
    /// Import list.
    Import,
}

/// One stacked lay: frame rows, hits, regions, top, heights, air.
type StackedLay = (
    Vec<Row>,
    Vec<Hit>,
    Vec<(String, usize, usize)>,
    Option<TopCache>,
    usize,
    usize,
    usize,
);

/// One dialog's build: kind, width, content, clicks, import ids, bar pairs.
type DialogBuild = (
    PopupKind,
    usize,
    Vec<Row>,
    Vec<Hit>,
    Vec<Option<ThemeId>>,
    Vec<(&'static str, &'static str)>,
);

/// A painted dialog: where it stands and what its clicks resolve through.
///
/// Origins recompute every refresh from the live size — the click map
/// never drifts from the paint.
#[derive(Clone, Debug)]
pub struct PopupLayout {
    /// Which dialog stands.
    pub kind: PopupKind,
    /// Dialog top-left, frame coords (outer edge, border included).
    pub ox: usize,
    /// Dialog top-left, frame coords (outer edge, border included).
    pub oy: usize,
    /// Content width (inside the border).
    pub w: usize,
    /// Content height (inside the border).
    pub h: usize,
    /// Clickable cells in dialog coords (small dialogs).
    pub hits: Vec<Hit>,
    /// Row-aligned theme ids (import dialog; `None` on group/chrome rows).
    pub import_ids: Vec<Option<ThemeId>>,
    /// Row-aligned title providers (import dialog; `None` on theme rows).
    pub import_groups: Vec<Option<String>>,
    /// No border chrome: the too-small fallback owns its rows alone.
    pub bare: bool,
    /// Import-dialog confirm button, frame coords (click-only `Enter`).
    pub confirm_btn: Option<Rect>,
    /// Import-dialog cancel button, frame coords (click-only `Esc`).
    pub cancel_btn: Option<Rect>,
}

/// Which body layout the last refresh mounted.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum BodyMode {
    /// The bare `draw_editor` stack (too small, capped out, or opted out).
    #[default]
    Bare,
    /// Compositor top plus full-width `THEME` / `EXAMPLES` panels.
    Stacked,
    /// Compositor top plus the side-by-side pair.
    Side,
}

/// The compositor top: bare logo + info column beside `EDITOR`.
///
/// Positions are body coords (the body starts at frame `(0, 0)`; the
/// footer docks below it). Button rects are what the click map checks —
/// everything else up here is chrome and answers to no click.
#[derive(Clone, Debug, Default)]
pub struct TopCache {
    /// Logo art width share.
    pub left_w: usize,
    /// Info column width share.
    pub info_w: usize,
    /// Editor panel outer width (borders included).
    pub editor_outer: usize,
    /// Screen rows the top occupies.
    pub top_screen: usize,
    /// Whether the head stacks chip over specimen (narrow box).
    pub stacked_head: bool,
    /// Logo art rows, `left_w` wide, `top_screen` tall.
    pub logo: Vec<Line<'static>>,
    /// Info column rows: theme meta + fill + two button rows.
    pub info: Vec<Line<'static>>,
    /// Editor inner rows (head + HSV bars + fill), content width.
    pub editor_inner: Vec<Line<'static>>,
    /// Editor content width (inside borders + air).
    pub editor_content_w: usize,
    /// Editor air per side inside the border.
    pub editor_pad: usize,
    /// Body x of each column.
    pub logo_x: usize,
    /// Body x of each column.
    pub info_x: usize,
    /// Body x of each column.
    pub editor_x: usize,
    /// `Themes` button, body coords (mouse mirror of `t`).
    pub themes_btn: Rect,
    /// `Import` button, body coords (mouse mirror of `i`).
    pub import_btn: Rect,
}

/// Stacked panels below the top (or below bare header chrome).
#[derive(Clone, Debug, Default)]
pub struct StackedCache {
    /// Bare header rows where no top fits (full width).
    pub header: Vec<Line<'static>>,
    /// Controls inner rows (`palette` + `interface` [+ `selected`]).
    pub controls: Vec<Line<'static>>,
    /// Live inner rows (`examples` + `diff` + `sample`).
    pub examples: Vec<Line<'static>>,
    /// Content air per side inside each panel border.
    pub pad: usize,
    /// Outer rects, body coords (borders included).
    pub controls_rect: Rect,
    /// Outer rects, body coords (borders included).
    pub examples_rect: Rect,
}

/// Side-by-side pair below the top (or below bare chrome).
#[derive(Clone, Debug, Default)]
pub struct SideCache {
    /// Bare header + selected rows where no top fits (full width).
    pub chrome: Vec<Line<'static>>,
    /// Left inner rows (palette pairs over interface pairs).
    pub left: Vec<Line<'static>>,
    /// Right inner rows (examples + diff + sample, fixed budget).
    pub right: Vec<Line<'static>>,
    /// Content air per side inside each panel border.
    pub pad: usize,
    /// Outer rects, body coords (borders included).
    pub left_rect: Rect,
    /// Outer rects, body coords (borders included).
    pub right_rect: Rect,
    /// Left content width (panel variant).
    pub left_w: usize,
    /// Right content width.
    pub right_inner: usize,
    /// Squeezed (`true`) or full (`false`) left pairs.
    pub narrow: bool,
}

/// One editor session under the compositor.
pub struct Shell {
    /// The session: buffer, picker, choice, warning — all behaviour.
    pub state: EditorState,
    /// The import popup's cursor and selection.
    pub import: ImportState,
    /// Route keys to the import popup while open.
    pub import_open: bool,
    /// The import list's viewport head, in column rows (titles counted).
    /// `None` re-seeds past the title on the next paint; the router
    /// clears it whenever the popup opens or closes.
    pub import_scroll: Option<usize>,
    /// The import confirm writer (truth files only).
    pub import_writer: Option<ImportWriter>,
    /// Per-theme failures plus writer problems, for the exit report.
    pub import_notes: Vec<String>,
    /// Live frame width, in columns.
    pub cols: usize,
    /// Live frame height, in rows.
    pub rows: usize,
    /// Clickable colour cells of the last body frame.
    pub hits: Vec<Hit>,
    /// Painted body lines (frame rows above the bar).
    pub body_lines: Vec<Line<'static>>,
    /// Painted footer bar lines (docked bottom).
    pub foot_lines: Vec<Line<'static>>,
    /// Painted dialog lines (content plus in-flow bar).
    pub popup_lines: Vec<Line<'static>>,
    /// Where the dialog stands, when one does.
    pub popup: Option<PopupLayout>,
    /// Blocks surviving the last layout (the `blocks` debug count).
    pub blocks: usize,
    /// Widest body row, in display columns.
    pub widest: usize,
    /// Which body layout the last refresh mounted.
    pub body_mode: BodyMode,
    /// Compositor top, when one stands.
    pub top: Option<TopCache>,
    /// Stacked panels, when they stand.
    pub stacked: Option<StackedCache>,
    /// Side-by-side pair, when it stands.
    pub side: Option<SideCache>,
    /// Live blocks standing collapsed (`examples`/`diff`/`sample`).
    /// Compositor state, not buffer state — the frame is always drawn
    /// whole and the collapsed rows are hidden, never unpainted, so a
    /// headless session never collapses.
    pub collapsed: std::collections::BTreeSet<String>,
    /// Live header rows of the last body frame: `(screen_y, x0, x1, block)`.
    /// A click inside one toggles its collapse, like `E`/`D`/`C`. The
    /// x-range matters where panels stand side by side at the same rows.
    pub live_headers: Vec<(u16, u16, u16, String)>,
}

impl Shell {
    /// A session over `state` with its import popup and writer.
    pub fn new(
        state: EditorState,
        import: ImportState,
        import_writer: Option<ImportWriter>,
    ) -> Self {
        let mut shell = Self {
            state,
            import,
            import_open: false,
            import_scroll: None,
            import_writer,
            import_notes: vec![],
            cols: 80,
            rows: 24,
            hits: vec![],
            body_lines: vec![],
            foot_lines: vec![],
            popup_lines: vec![],
            popup: None,
            blocks: 0,
            widest: 0,
            body_mode: BodyMode::Bare,
            top: None,
            stacked: None,
            side: None,
            collapsed: std::collections::BTreeSet::new(),
            live_headers: vec![],
        };
        shell.refresh();
        shell
    }

    /// Which surface owns the keys right now.
    pub fn screen(&self) -> Screen {
        if self.import_open {
            Screen::Import
        } else if self.state.setup.is_some() {
            Screen::Setup
        } else if self.state.overrides.is_some() {
            Screen::Overrides
        } else if self.state.overlay.is_some() {
            Screen::Themes
        } else {
            Screen::Editor
        }
    }

    /// Apply a live size. Logs the `resize to WxH` debug line on change.
    pub fn set_size(&mut self, cols: u16, rows: u16) {
        let (cols, rows) = (cols as usize, rows as usize);
        if cols != self.cols || rows != self.rows {
            self.cols = cols;
            self.rows = rows;
            debug::resized(&format!("{cols}x{rows}"));
        }
    }

    /// The footer pairs for the screen on top: always the editor's.
    ///
    /// The docked bar lives on the stack bottom with the frame, so a
    /// dialog dims it along with everything behind it — the dialog's own
    /// hints ride its in-flow bar instead, never the docked one.
    fn footer_pairs(&self) -> Vec<(String, String)> {
        // Owned: the editor source builds a fresh `Vec` per frame, so no
        // branch can hand out borrows into a temporary.
        editor_hints(self.state.undo.len())
    }

    /// State → cells: grid, body frame, footer bar, dialog layout.
    pub fn refresh(&mut self) {
        let cols = self.cols;
        let rows = self.rows;
        let slots = self.state.slots.clone();

        let pairs = self.footer_pairs();
        let pair_refs: Vec<(&str, &str)> = pairs
            .iter()
            .map(|(key, what)| (key.as_str(), what.as_str()))
            .collect();
        let status = self.state.status.clone();
        // Below one row past the floor there is no row for the bar:
        // the frame (or the fallback) owns the whole window instead.
        let show_footer = cols >= MIN_COLS && rows > MIN_ROWS;
        self.foot_lines = if show_footer {
            vec![bar_line_single(&pair_refs, &status, cols, &slots)]
        } else {
            vec![]
        };
        let body_rows = rows.saturating_sub(self.foot_lines.len());

        // Reset the compositor caches; each path below sets what stands.
        self.body_mode = BodyMode::Bare;
        self.top = None;
        self.stacked = None;
        self.side = None;
        self.hits = vec![];
        self.blocks = 0;
        self.widest = 0;
        self.live_headers = vec![];

        // Below the floor the frame is one centred hint and nothing
        // else: the hint stays fully unstyled and the bar stays down,
        // so the frame owns the whole window.

        // Layout size (§15.7): past the maxima content stops reflowing
        // and the extra screen stays fill.
        let (layout_cols, layout_rows) = if panel_limits_enabled() {
            (cols.min(PANEL_MAX_W), rows.min(PANEL_MAX_H))
        } else {
            (cols, rows)
        };
        let foot_len = self.foot_lines.len();
        let frame_h = layout_rows.saturating_sub(foot_len);

        let panels_wanted = panels_enabled() && cols >= MIN_COLS && rows >= MIN_ROWS;
        if cols < MIN_COLS || rows < MIN_ROWS {
            self.body_lines = small_lines(cols, body_rows, &slots);
        } else if panels_wanted && self.try_side(layout_cols, frame_h, body_rows) {
            // Side-by-side mounted everything; grid + counts set inside.
        } else if panels_wanted
            && cols >= MIN_COLS + 2
            && rows >= MIN_ROWS + 5
            && self.try_stacked(layout_cols, frame_h, body_rows)
        {
            // Stacked panels mounted; grid + counts set inside.
        } else {
            self.refresh_bare(cols, body_rows);
        }

        // The frame may return fewer rows than the area holds: the rest
        // is buffer-background fill, never the terminal's background.
        while self.body_lines.len() < body_rows {
            self.body_lines.push(blank_line(cols, &slots));
        }

        self.refresh_popup();
        debug::redraw(
            cols,
            rows,
            self.body_lines.len(),
            self.blocks,
            self.state.sel,
            self.widest,
        );
    }

    /// The bare stack: `draw_editor` rows full-width, no panels.
    fn refresh_bare(&mut self, cols: usize, body_rows: usize) {
        self.state.grid = grid_geometry(cols, "  ");
        let slots = self.state.slots.clone();
        let status = self.state.status.clone();
        let opts = FrameOpts {
            label: head_label(&self.state),
            path: session_path(&self.state),
            sel: self.state.sel,
            undo_len: self.state.undo.len(),
            status,
            cols,
            rows: body_rows,
            use_banner: None,
            indent: "  ".to_owned(),
            footer: false,
        };
        let out = frame::frame(&opts, &self.state.slots);
        // The bare stack mounts live blocks collapsible too: the header
        // stands in for the frame's title row, shut blocks keep the
        // header only. Rows outside every region stand as painted.
        let collapsible = collapsible_enabled();
        // `new_y[y]`: the built row a kept frame row lands on, or
        // `None` for hidden rows (shut bodies, replaced titles).
        let mut new_y: Vec<Option<usize>> = vec![None; out.rows.len()];
        let mut bare_rows: Vec<Row> = vec![];
        let mut region_of: Vec<Option<usize>> = vec![None; out.rows.len()];
        for (i, (_, first, count)) in out.regions.iter().enumerate() {
            let stop = (*first + *count).min(out.rows.len());
            for slot in region_of[*first..stop].iter_mut() {
                *slot = Some(i);
            }
        }
        let mut r = 0usize;
        while r < out.rows.len() {
            let live = match region_of[r] {
                Some(i) => {
                    let (name, first, _) = &out.regions[i];
                    collapsible && live_title(name).is_some() && *first == r
                }
                None => false,
            };
            if live {
                let (name, first, count) = &out.regions[region_of[r].unwrap()];
                let name = name.clone();
                let stop = (*first + *count).min(out.rows.len());
                let shut = self.collapsed.contains(&name);
                self.live_headers
                    .push((bare_rows.len() as u16, 0, self.cols as u16, name.clone()));
                bare_rows.push(live_header_row(&name, shut, &slots));
                if !shut {
                    let base = bare_rows.len();
                    for (i, y) in (first + 1..stop).enumerate() {
                        new_y[y] = Some(base + i);
                        bare_rows.push(out.rows[y].clone());
                    }
                }
                r = stop;
            } else {
                new_y[r] = Some(bare_rows.len());
                bare_rows.push(out.rows[r].clone());
                r += 1;
            }
        }
        let mut bare_hits = vec![];
        for hit in &out.hits {
            if let Some(y) = new_y.get(hit.y).copied().flatten() {
                bare_hits.push(Hit {
                    y,
                    x0: hit.x0,
                    x1: hit.x1,
                    slot: hit.slot,
                });
            }
        }
        self.widest = bare_rows
            .iter()
            .map(|row| row_visible(row))
            .max()
            .unwrap_or(0);
        // Code-sample rows pad in their own last ink (the frame
        // parser's unclosed trailing style); every other row pads plain.
        if let Some((_, first, count)) = out.regions.iter().find(|(name, _, _)| name == "sample") {
            let (first, count) = (*first, *count);
            let stop = (first + count).min(new_y.len());
            for y in first.saturating_add(1)..stop {
                if let Some(by) = new_y.get(y).copied().flatten() {
                    if let Some(row) = bare_rows.get_mut(by) {
                        *row = pad_code_row(std::mem::take(row), &slots, cols);
                    }
                }
            }
        }
        self.blocks = out.regions.len();
        self.hits = bare_hits;
        self.body_lines = pad_lines(render_rows(&bare_rows, &slots), cols, &slots);
        self.body_mode = BodyMode::Bare;
    }

    /// The compositor top at `layout_cols`, or `None` for the bare stack.
    ///
    /// Bare logo beside the info column beside one bordered `EDITOR`
    /// panel (readout head + three HSV bars). Shares come from the one
    /// allocator (`top_layout`); past the tightest share the caller keeps
    /// the bare stack. `HUEBOX_TOP=0` / `HUEBOX_EDITOR_PANEL=0` pin that
    /// stack. `top_pad` is decision 45's air, reserved out of the info
    /// share so a padded box mounts wherever the unpadded one does.
    fn build_top(&self, layout_cols: usize, top_pad: usize) -> Option<TopCache> {
        if !editor_panel_enabled() || !top_enabled() {
            return None;
        }
        let slots = &self.state.slots;
        let sel = self.state.sel;
        let head = head_label(&self.state);
        let (label, path) = top_subject(&self.state, &head);
        let (left_w, info_w, editor_outer, stacked) =
            top_layout(layout_cols, &label, &path, slots, sel, top_pad)?;
        let left_raw = top_left_rows(slots, left_w);
        let (meta_rows, head_rows, _) =
            top_editor_meta(&label, &path, slots, sel, Some(info_w), stacked);
        let head_w = head_rows
            .iter()
            .map(|row| row_visible(row))
            .max()
            .unwrap_or(0);
        let editor_pad = EDITOR_PAD_X + top_pad;
        let content_w = editor_outer.saturating_sub(2 + 2 * editor_pad);
        if content_w < head_w {
            return None;
        }
        let single_chrome = "hue".len() + 1 + HSV_FIELD + 2;
        let bar_w = content_w.saturating_sub(single_chrome);
        if bar_w < 7 {
            return None;
        }
        let key = SLOTS[sel.min(SLOTS.len() - 1)];
        let value = slot_hex(slots, key);
        let mut hsv_rows = Vec::new();
        for axis in 0..3 {
            let row = hsv_axis(slots, value, axis, bar_w);
            if row.is_empty() {
                return None;
            }
            hsv_rows.push(row);
        }
        let mut editor_rows = head_rows;
        editor_rows.extend(hsv_rows);
        let top_screen = 6.max(left_raw.len()).max(editor_rows.len() + 2);
        let inner_h = top_screen.saturating_sub(2);
        if inner_h < editor_rows.len() {
            return None;
        }

        // Rows, all standing on the buffer background at their own widths.
        let mut logo_backed: Vec<Row> = left_raw
            .into_iter()
            .map(|row| backdrop(&row, slots, left_w))
            .collect();
        while logo_backed.len() < top_screen {
            logo_backed.push(backdrop(&[], slots, left_w));
        }
        logo_backed.truncate(top_screen);
        let mut meta_backed: Vec<Row> = meta_rows
            .into_iter()
            .map(|row| backdrop(&row, slots, info_w))
            .collect();
        // Info column: meta, then fill, then Themes, one blank row, and
        // Import — Python's import button carries `margin-top: 1`, so the
        // blank parts the buttons and all three ride the column's last
        // rows, costing no row.
        let btn_h = 3usize;
        while meta_backed.len() + btn_h < top_screen {
            meta_backed.push(backdrop(&[], slots, info_w));
        }
        if meta_backed.len() + btn_h > top_screen {
            // No room for both buttons: the top does not stand.
            return None;
        }
        let mut editor_backed: Vec<Row> = editor_rows
            .into_iter()
            .map(|row| {
                let inner = backdrop(&row, slots, content_w);
                with_side_air(inner, editor_pad, slots)
            })
            .collect();
        while editor_backed.len() < inner_h {
            editor_backed.push(with_side_air(
                backdrop(&[], slots, content_w),
                editor_pad,
                slots,
            ));
        }
        editor_backed.truncate(inner_h);

        let logo_x = 0;
        let info_x = left_w;
        let editor_x = left_w + info_w;
        if editor_x + editor_outer > layout_cols {
            return None;
        }
        // Buttons: flat `Themes` / `Import` under the theme subject, each
        // exactly what its key does through the same call. Never
        // focusable; painted only in the selection pair. The button
        // spans the share with one air cell each side — a clipped
        // button still answers its click.
        let air = if info_w > 2 { 1 } else { 0 };
        let btn_w = info_w.saturating_sub(2 * air);
        if btn_w == 0 {
            return None;
        }
        let info_rows = meta_backed;
        // Button rects: the click map checks these spans only.
        // Style the button rows with the selection pair at paint time;
        // the rows above stay ordinary chrome rows.
        let themes_btn = Rect::new(
            (info_x + air) as u16,
            (top_screen.saturating_sub(3)) as u16,
            btn_w as u16,
            1,
        );
        let import_btn = Rect::new(
            (info_x + air) as u16,
            (top_screen.saturating_sub(1)) as u16,
            btn_w as u16,
            1,
        );

        let logo = render_rows(&logo_backed, slots);
        let mut info = render_rows(&info_rows, slots);
        // Buttons as air + box + air: one air cell of margin each side,
        // the box spanning the rest of the share; the blank between
        // stands (Python's import-button top margin).
        info.push(button_row_in(info_w, air, btn_w, "Themes", slots));
        info.push(render_rows(&[backdrop(&[], slots, info_w)], slots).remove(0));
        info.push(button_row_in(info_w, air, btn_w, "Import", slots));
        debug_assert_eq!(info.len(), top_screen, "button rows cost no row");
        let editor_inner = render_rows(&editor_backed, slots);

        Some(TopCache {
            left_w,
            info_w,
            editor_outer,
            top_screen,
            stacked_head: stacked,
            logo,
            info,
            editor_inner,
            editor_content_w: content_w,
            editor_pad,
            logo_x,
            info_x,
            editor_x,
            themes_btn,
            import_btn,
        })
    }

    /// Stacked panels at the layout size; `false` keeps the bare stack.
    fn try_stacked(&mut self, layout_cols: usize, frame_h: usize, body_rows: usize) -> bool {
        // Decision 45: padded where it shows everything the unpadded lay
        // does, else unpadded, else the bare stack stands.
        let unpadded = self.lay_stacked(layout_cols, frame_h, 0);
        let Some(base) = unpadded else {
            return false;
        };
        let choice = if panel_pad_enabled() && layout_cols >= PANEL_PAD_MIN_W {
            match self.lay_stacked(layout_cols, frame_h, PANEL_PAD) {
                Some(airy) if pad_keeps_content(&base, &airy) => airy,
                _ => base,
            }
        } else {
            base
        };
        self.mount_stacked(choice, layout_cols, body_rows);
        true
    }

    /// One stacked lay at `pad` air per side, or `None`.
    fn lay_stacked(&self, layout_cols: usize, frame_h: usize, pad: usize) -> Option<StackedLay> {
        let inner_w = layout_cols.checked_sub(2 + 2 * pad)?;
        if inner_w < MIN_COLS {
            return None;
        }
        let mut inner_h = frame_h.saturating_sub(5).max(MIN_ROWS);
        for _ in 0..3 {
            if inner_h < MIN_ROWS {
                return None;
            }
            let status = self.state.status.clone();
            let opts = FrameOpts {
                label: head_label(&self.state),
                path: session_path(&self.state),
                sel: self.state.sel,
                undo_len: self.state.undo.len(),
                status,
                cols: inner_w,
                rows: inner_h,
                use_banner: None,
                indent: String::new(),
                footer: false,
            };
            let out = frame::frame(&opts, &self.state.slots);
            let by_name = |name: &str| {
                out.regions
                    .iter()
                    .find(|(key, _, _)| key == name)
                    .map(|(_, first, count)| (*first, *count))
            };
            let mut header_here = by_name("header").map(|(_, count)| count).unwrap_or(0);
            if let (Some((sel_first, sel_count)), Some((pal_first, _))) =
                (by_name("selected"), by_name("palette"))
            {
                if sel_first < pal_first {
                    header_here += sel_count;
                }
            }
            let top_pad = pad;
            let top = self.build_top(layout_cols, top_pad);
            let top_screen_here = top
                .as_ref()
                .map(|top| top.top_screen)
                .unwrap_or(header_here);
            let has_examples = ["examples", "diff", "sample"]
                .iter()
                .any(|name| by_name(name).is_some());
            let panels_here = if has_examples { 2 } else { 1 };
            let need = top_screen_here
                .saturating_sub(header_here)
                .saturating_add(panels_here * 2);
            if inner_h.saturating_add(need) <= frame_h {
                return Some((
                    out.rows,
                    out.hits,
                    out.regions,
                    top,
                    top_screen_here,
                    header_here,
                    pad,
                ));
            }
            inner_h = frame_h.saturating_sub(need);
        }
        None
    }

    /// Mount a stacked lay: slice the frame rows into bordered panels.
    #[allow(clippy::too_many_lines)]
    fn mount_stacked(&mut self, lay: StackedLay, layout_cols: usize, body_rows: usize) {
        let (rows, hits, regions, top, top_screen, header_h, pad) = lay;
        let slots = self.state.slots.clone();
        let by_name = |name: &str| {
            regions
                .iter()
                .find(|(key, _, _)| key == name)
                .map(|(_, first, count)| (*first, *count))
        };
        // The side top block rides with the header chrome: where the
        // frame put `selected` above the palette it is the header's right
        // column, not a control.
        let mut top_names = vec!["header"];
        if let (Some((sel_first, _)), Some((pal_first, _))) =
            (by_name("selected"), by_name("palette"))
        {
            if sel_first < pal_first {
                top_names.push("selected");
            }
        }
        let in_top = |name: &str| top_names.contains(&name);
        let controls_names = ["palette", "interface", "selected"];
        let examples_names = ["examples", "diff", "sample"];
        let mut controls_blocks = vec![];
        let mut examples_blocks = vec![];
        let mut header_blocks = vec![];
        for (name, first, count) in &regions {
            if in_top(name) {
                header_blocks.push((name.clone(), *first, *count));
            } else if controls_names.contains(&name.as_str()) {
                controls_blocks.push((name.clone(), *first, *count));
            } else if examples_names.contains(&name.as_str()) {
                examples_blocks.push((name.clone(), *first, *count));
            } else if name == "hints" || name == "status" {
                // Footer lives docked; nothing mounts it here.
            } else {
                // A future widget stays chrome rather than vanishing.
                header_blocks.push((name.clone(), *first, *count));
            }
        }
        let inner_w = layout_cols.saturating_sub(2 + 2 * pad);
        let take = |blocks: &[(String, usize, usize)]| -> Vec<Row> {
            let mut out = vec![];
            for (_, first, count) in blocks {
                out.extend(rows[*first..(*first + *count).min(rows.len())].to_vec());
            }
            out
        };
        let controls_rows = take(&controls_blocks);
        let header_rows = take(&header_blocks);
        // Hits remap: content coords → frame coords past border + air,
        // stacked below the top at the panel's inner origin.
        let mut frame_hits = vec![];
        let mut controls_offset = 0usize;
        for (_, first, count) in &controls_blocks {
            for hit in hits
                .iter()
                .filter(|hit| hit.y >= *first && hit.y < first + count)
            {
                let row_in_block = hit.y - first;
                frame_hits.push(Hit {
                    y: top_screen + 1 + controls_offset + row_in_block,
                    x0: hit.x0 + 1 + pad,
                    x1: hit.x1 + 1 + pad,
                    slot: hit.slot,
                });
            }
            controls_offset += count;
        }
        // Live blocks mount collapsible: the header stands in for the
        // frame's title row, so an open block costs its rows and a shut
        // one its header. Open maps 1:1 past the replaced title (chrome,
        // hitless); shut drops the block's hits with its rows.
        let collapsible = collapsible_enabled();
        let mut built: Vec<(String, Vec<Row>, usize, usize)> = vec![];
        for (name, first, count) in &examples_blocks {
            let mut block_rows: Vec<Row> = vec![];
            if collapsible && live_title(name).is_some() {
                block_rows.push(live_header_row(name, self.collapsed.contains(name), &slots));
                if !self.collapsed.contains(name) {
                    block_rows.extend(rows[*first + 1..(*first + *count).min(rows.len())].to_vec());
                }
            } else {
                block_rows.extend(rows[*first..(*first + *count).min(rows.len())].to_vec());
            }
            built.push((name.clone(), block_rows, *first, *count));
        }
        let examples_y0 =
            top_screen + (controls_rows.len() + 2) * usize::from(!controls_rows.is_empty());
        let mut examples_offset = 0usize;
        for (name, block_rows, first, count) in &built {
            let live = collapsible && live_title(name).is_some();
            let shut = live && self.collapsed.contains(name);
            if live {
                self.live_headers.push((
                    (examples_y0 + 1 + examples_offset) as u16,
                    (1 + pad) as u16,
                    (1 + pad + inner_w) as u16,
                    name.clone(),
                ));
            }
            if !shut {
                for hit in hits
                    .iter()
                    .filter(|hit| hit.y >= *first && hit.y < first + count)
                {
                    let row_in_block = hit.y - first;
                    // The title row stands replaced by header chrome.
                    if live && row_in_block == 0 {
                        continue;
                    }
                    frame_hits.push(Hit {
                        y: examples_y0 + 1 + examples_offset + row_in_block,
                        x0: hit.x0 + 1 + pad,
                        x1: hit.x1 + 1 + pad,
                        slot: hit.slot,
                    });
                }
            }
            examples_offset += block_rows.len();
        }
        let examples_rows: Vec<Row> = built
            .into_iter()
            .flat_map(|(name, block_rows, _, _)| {
                let slots = slots.clone();
                block_rows.into_iter().enumerate().map(move |(i, row)| {
                    // Code rows pad in their own last ink; the block's
                    // title row and every other block pad plain.
                    if name == "sample" && i > 0 {
                        pad_code_row(row, &slots, inner_w)
                    } else {
                        row
                    }
                })
            })
            .collect();
        // Lines: content rows backdropped at the inner width, then aired
        // past the borders. Chrome rows re-back to the full layout width.
        let air_row = |row: Row| with_side_air(row, pad, &slots);
        let controls_backed: Vec<Row> = controls_rows
            .into_iter()
            .map(|row| air_row(backdrop(&row, &slots, inner_w)))
            .collect();
        let examples_backed: Vec<Row> = examples_rows
            .into_iter()
            .map(|row| air_row(backdrop(&row, &slots, inner_w)))
            .collect();
        let header_backed: Vec<Row> = header_rows
            .into_iter()
            .map(|row| backdrop(&row, &slots, layout_cols))
            .collect();

        let controls_h = controls_backed.len();
        let examples_h = examples_backed.len();
        let controls_y = top_screen;
        let examples_y = controls_y + (controls_h + 2) * usize::from(controls_h > 0);
        let total = examples_y + (examples_h + 2) * usize::from(examples_h > 0);
        if total > body_rows {
            // No honest panel below usable: the caller keeps the bare
            // stack instead of squeezing one.
            self.refresh_bare(self.cols, body_rows);
            return;
        }

        let controls = render_rows(&controls_backed, &slots);
        let examples = render_rows(&examples_backed, &slots);
        let header = render_rows(&header_backed, &slots);
        let controls_rect = Rect::new(
            0,
            controls_y as u16,
            layout_cols as u16,
            (controls_h + 2) as u16,
        );
        let examples_rect = Rect::new(
            0,
            examples_y as u16,
            layout_cols as u16,
            (examples_h + 2) as u16,
        );

        self.state.grid = grid_geometry(inner_w, "");
        self.hits = frame_hits;
        self.blocks = regions.len();
        self.widest = rows.iter().map(|row| row_visible(row)).max().unwrap_or(0);
        self.top = top;
        self.stacked = Some(StackedCache {
            header,
            controls,
            examples,
            pad,
            controls_rect,
            examples_rect,
        });
        self.body_mode = BodyMode::Stacked;
        // `body_lines` feeds the debug count + the fill below the panels.
        self.body_lines = vec![];
        let _ = header_h;
    }

    /// Side-by-side pair at the layout size; `false` tries stacked next.
    fn try_side(&mut self, layout_cols: usize, frame_h: usize, body_rows: usize) -> bool {
        let (narrow, show_hex) = if layout_cols >= 100 {
            (false, true)
        } else if layout_cols >= 80 {
            (true, true)
        } else if layout_cols >= MIN_COLS {
            (true, false)
        } else {
            return false;
        };
        let (left_w, left_outer) = if !narrow {
            (SIDE_LEFT_W, SIDE_LEFT_W + 2)
        } else if show_hex {
            (SIDE_LEFT_NARROW_W, SIDE_LEFT_NARROW_W + 2)
        } else {
            (SIDE_LEFT_THIN_W, SIDE_LEFT_THIN_W + 2)
        };
        // Chrome above the pair: the compositor top where it fits, else
        // the bare header + selected block full-width.
        let top_pad = if panel_pad_enabled() && layout_cols >= PANEL_PAD_MIN_W {
            PANEL_PAD
        } else {
            0
        };
        let top = self.build_top(layout_cols, top_pad);
        let chrome_h;
        let mut chrome_backed: Vec<Row> = vec![];
        if let Some(top) = &top {
            chrome_h = top.top_screen;
        } else {
            // Bare chrome: header + selected from a full-width frame run
            // — captured, like everywhere, never re-derived.
            let status = self.state.status.clone();
            let opts = FrameOpts {
                label: head_label(&self.state),
                path: session_path(&self.state),
                sel: self.state.sel,
                undo_len: self.state.undo.len(),
                status,
                cols: layout_cols,
                rows: frame_h,
                use_banner: None,
                indent: "  ".to_owned(),
                footer: false,
            };
            let out = frame::frame(&opts, &self.state.slots);
            let mut need = vec![];
            for (name, first, count) in &out.regions {
                if name == "header" || name == "selected" {
                    need.push((*first, *count));
                }
            }
            if need.is_empty() {
                return false;
            }
            for (first, count) in &need {
                chrome_backed
                    .extend(out.rows[*first..(*first + *count).min(out.rows.len())].to_vec());
            }
            // A trimmed frame keeps the stacked layout instead.
            if need.len() < 2 {
                return false;
            }
            chrome_h = chrome_backed.len();
        }
        let content_h = frame_h.saturating_sub(chrome_h + 2);
        // One height bar for both widths: short windows keep the stacked
        // panels whatever the width.
        if content_h < SIDE_LEFT_ROWS {
            return false;
        }
        let pad = if panel_pad_enabled()
            && layout_cols.saturating_sub(left_outer + 2 * PANEL_PAD + 2 + 2 * PANEL_PAD) >= 1
        {
            PANEL_PAD
        } else {
            0
        };
        let right_outer = layout_cols.saturating_sub(left_outer + 2 * pad);
        let right_inner = right_outer.saturating_sub(2 + 2 * pad);
        if right_inner < 1 {
            return false;
        }
        let slots = self.state.slots.clone();
        let mut right_regions = vec![];
        let right_rows =
            match side_live_rows(&slots, right_inner, content_h, Some(&mut right_regions)) {
                Some(rows) => rows,
                None => return false,
            };
        let mut left_hits: Vec<Hit> = vec![];
        let left_rows = side_left_rows(
            &slots,
            Some(self.state.sel),
            Some(&mut left_hits),
            0,
            narrow,
            show_hex,
        );
        let left_expected = if narrow {
            SIDE_LEFT_NARROW_ROWS
        } else {
            SIDE_LEFT_ROWS
        };
        if left_rows.len() != left_expected {
            return false;
        }
        // Success past this point: remap the left hits into frame coords
        // (right blocks name no slot), mount the pair.
        let pair_y = chrome_h;
        let left_x0 = 1 + pad;
        let frame_hits: Vec<Hit> = left_hits
            .into_iter()
            .map(|hit| Hit {
                y: pair_y + 1 + hit.y,
                x0: hit.x0 + left_x0,
                x1: hit.x1 + left_x0,
                slot: hit.slot,
            })
            .collect();
        let air = |row: Row, content_w: usize| {
            with_side_air(backdrop(&row, &slots, content_w), pad, &slots)
        };
        let left_backed: Vec<Row> = left_rows.into_iter().map(|row| air(row, left_w)).collect();
        // Left fill below the pairs: the panel holds its height.
        let mut left_padded = left_backed;
        while left_padded.len() < content_h {
            left_padded.push(air(vec![], left_w));
        }
        left_padded.truncate(content_h);
        // Live blocks mount collapsible like the stacked pair: the
        // header stands in for the frame's title row, shut blocks keep
        // their header only, and the panel holds its height with fill.
        let collapsible = collapsible_enabled();
        let mut right_built: Vec<Row> = vec![];
        let mut right_offset = 0usize;
        for (name, first, count) in &right_regions {
            if collapsible && live_title(name).is_some() {
                let shut = self.collapsed.contains(name);
                let right_x0 = left_outer + 2 * pad;
                self.live_headers.push((
                    (pair_y + 1 + right_offset) as u16,
                    (right_x0 + 1 + pad) as u16,
                    (right_x0 + 1 + pad + right_inner) as u16,
                    name.clone(),
                ));
                right_built.push(live_header_row(name, shut, &slots));
                if !shut {
                    right_built.extend(
                        right_rows[*first + 1..(*first + *count).min(right_rows.len())].to_vec(),
                    );
                }
                right_offset += 1 + if shut {
                    0
                } else {
                    (*first + *count)
                        .min(right_rows.len())
                        .saturating_sub(*first + 1)
                };
            } else {
                let body = right_rows[*first..(*first + *count).min(right_rows.len())].to_vec();
                right_offset += body.len();
                right_built.extend(body);
            }
        }
        let right_backed: Vec<Row> = right_built
            .into_iter()
            .map(|row| air(row, right_inner))
            .collect();
        // Right fill below shut blocks: the panel holds its height.
        let mut right_padded = right_backed;
        while right_padded.len() < content_h {
            right_padded.push(air(vec![], right_inner));
        }
        right_padded.truncate(content_h);
        let chrome_lines = render_rows(
            &chrome_backed
                .into_iter()
                .map(|row| backdrop(&row, &slots, layout_cols))
                .collect::<Vec<Row>>(),
            &slots,
        );
        let left = render_rows(&left_padded, &slots);
        let right = render_rows(&right_padded, &slots);
        let left_rect = Rect::new(
            0,
            pair_y as u16,
            (left_outer + 2 * pad) as u16,
            (content_h + 2) as u16,
        );
        let right_rect = Rect::new(
            (left_outer + 2 * pad) as u16,
            pair_y as u16,
            right_outer as u16,
            (content_h + 2) as u16,
        );
        if pair_y + content_h + 2 > body_rows {
            return false;
        }
        self.state.grid = side_grid();
        self.hits = frame_hits;
        self.blocks = 2 + right_regions.len();
        self.widest = layout_cols;
        self.top = top;
        self.side = Some(SideCache {
            chrome: chrome_lines,
            left,
            right,
            pad,
            left_rect,
            right_rect,
            left_w,
            right_inner,
            narrow,
        });
        self.body_mode = BodyMode::Side;
        self.body_lines = vec![];
        true
    }

    /// The dialog over the dimmed base: content rows plus its in-flow bar.
    fn refresh_popup(&mut self) {
        self.popup = None;
        self.popup_lines = vec![];
        let cols = self.cols;
        let rows = self.rows;
        let slots = self.state.slots.clone();
        let status = self.state.status.clone();
        // Content budget: the dialog border plus its bar always fit; the
        // list windows into whatever height is left.
        let bar_height = |pairs: &[(&str, &str)]| {
            fold_footer(pairs, &status, cols.min(OVERRIDES_DIALOG_W))
                .len()
                .max(1)
        };
        let (kind, dlg_w, content, hits, ids, pairs): DialogBuild = match self.screen() {
            Screen::Editor => return,
            Screen::Setup => {
                let pairs = setup_pairs(self.state.setup_name.is_some()).to_vec();
                let dlg_w = clamp_width(SETUP_DIALOG_W, cols);
                let budget = rows.saturating_sub(2 + bar_height(&pairs)).max(1);
                let frame = setup_lines(
                    self.state.setup.unwrap_or(0),
                    dlg_w,
                    budget,
                    &status,
                    &slots,
                    self.state.setup_name.as_deref(),
                    false,
                );
                (
                    PopupKind::Setup,
                    dlg_w,
                    frame.rows,
                    frame.hits,
                    vec![],
                    pairs,
                )
            }
            Screen::Themes => {
                let pairs = themes_pairs().to_vec();
                let dlg_w = clamp_width(THEMES_DIALOG_W, cols);
                let budget = rows
                    .saturating_sub(2 + bar_height(&pairs))
                    .clamp(1, THEMES_DIALOG_MAX_H);
                let (names, index, current) =
                    self.state
                        .picker_frame()
                        .unwrap_or((vec![], 0, String::new()));
                let frame = theme_lines(
                    &names, index, &current, dlg_w, budget, &status, &slots, false,
                );
                (
                    PopupKind::Themes,
                    dlg_w,
                    frame.rows,
                    frame.hits,
                    vec![],
                    pairs,
                )
            }
            Screen::Overrides => {
                let pairs = overrides_pairs().to_vec();
                let dlg_w = clamp_width(OVERRIDES_DIALOG_W, cols);
                let budget = rows.saturating_sub(2 + bar_height(&pairs)).max(1);
                let over = self.state.overrides.clone().unwrap_or_default();
                let frame = overrides_lines(
                    &over.main,
                    &over.keys,
                    self.state.overrides_index,
                    dlg_w,
                    budget,
                    &status,
                    &slots,
                    false,
                );
                (
                    PopupKind::Overrides,
                    dlg_w,
                    frame.rows,
                    frame.hits,
                    vec![],
                    pairs,
                )
            }
            Screen::Import => {
                self.refresh_import(cols, rows);
                return;
            }
        };
        let bar = bar_lines(&pairs, &status, dlg_w, &slots);
        let mut lines = render_rows(&content, &slots);
        lines.extend(bar);
        // Import dialog footer buttons: click-only mirrors of `Enter`
        // (confirm) / `Esc` (cancel) through the same key surface.
        let mut confirm_range: Option<(usize, usize)> = None;
        let mut cancel_range: Option<(usize, usize)> = None;
        if kind == PopupKind::Import {
            let (btn_line, confirm, cancel) = import_footer_buttons(dlg_w, &slots);
            confirm_range = Some(confirm);
            cancel_range = Some(cancel);
            lines.push(btn_line);
        }
        let rect = popup_rect(
            Rect::new(0, 0, cols as u16, rows as u16),
            (dlg_w + 2) as u16,
            (lines.len() + 2) as u16,
        );
        // The clamp binds on tiny screens: pad and click in the width
        // that actually stands, never the width that was asked for.
        let inner_w = rect.width.saturating_sub(2) as usize;
        lines.truncate(rect.height.saturating_sub(2) as usize);
        while lines.len() < rect.height.saturating_sub(2) as usize {
            lines.push(blank_line(inner_w, &slots));
        }
        // Button rects in frame coords, for the click map.
        let (confirm_btn, cancel_btn) = match (confirm_range, cancel_range) {
            (Some((cx0, cx1)), Some((gx0, gx1))) => {
                let inner_h = lines.len();
                if inner_h == 0 {
                    (None, None)
                } else {
                    let by = rect.y as usize + 1 + inner_h - 1;
                    (
                        Some(Rect::new(
                            (rect.x as usize + 1 + cx0) as u16,
                            by as u16,
                            cx1.saturating_sub(cx0) as u16,
                            1,
                        )),
                        Some(Rect::new(
                            (rect.x as usize + 1 + gx0) as u16,
                            by as u16,
                            gx1.saturating_sub(gx0) as u16,
                            1,
                        )),
                    )
                }
            }
            _ => (None, None),
        };
        self.popup_lines = pad_lines(lines, inner_w, &slots);
        self.popup = Some(PopupLayout {
            kind,
            ox: rect.x as usize,
            oy: rect.y as usize,
            w: inner_w,
            h: self.popup_lines.len(),
            hits,
            import_ids: ids,
            import_groups: vec![],
            confirm_btn,
            cancel_btn,
            bare: false,
        });
    }

    /// The import dialog over the dimmed base: full-screen list plus live
    /// preview plus its in-flow bar and buttons — one bordered dialog,
    /// the same kind as every other popup, at the import size.
    fn refresh_import(&mut self, cols: usize, rows: usize) {
        use huebox_render::floor::too_small_frame;
        let slots = self.state.slots.clone();
        // Below the floor the popup keeps the editor's own fallback —
        // the hint line instead of a squeezed modal, no border chrome.
        if cols < MIN_COLS || rows < MIN_ROWS {
            let hint: String = too_small_frame(cols).iter().map(|cell| cell.ch).collect();
            self.popup_lines = vec![Line::from(vec![ratatui::text::Span::styled(
                hint,
                Style::new()
                    .fg(ratatui::style::Color::Reset)
                    .bg(ratatui::style::Color::Reset),
            )])];
            self.popup = Some(PopupLayout {
                kind: PopupKind::Import,
                ox: 0,
                oy: 0,
                w: cols,
                h: 1,
                hits: vec![],
                import_ids: vec![],
                import_groups: vec![],
                confirm_btn: None,
                cancel_btn: None,
                bare: true,
            });
            return;
        }
        let pairs: Vec<(&str, &str)> = IMPORT_HINTS.to_vec();
        let dlg_w = clamp_width(IMPORT_DIALOG_W, cols);
        let dlg_h = rows.saturating_sub(2).min(IMPORT_DIALOG_MAX_H);
        let count = import_count_text(&self.import);
        let bar = bar_lines(&pairs, &count, dlg_w, &slots);
        // The buttons cost one row inside the dialog, like every bar.
        let body_h = dlg_h.saturating_sub(bar.len() + 1).max(1);
        let left_w = IMPORT_LEFT_W.min(dlg_w.saturating_sub(8));
        let preview_w = dlg_w.saturating_sub(left_w);
        // The viewport head: past the title while overflowing, then
        // minimal to the cursor, clamped when the column shrinks.
        let col_h = import_column_height(&self.import);
        let mut scroll = self
            .import_scroll
            .unwrap_or(if col_h > body_h { 1 } else { 0 });
        if let Some(cy) = import_cursor_y(&self.import) {
            if cy < scroll {
                scroll = cy;
            } else if body_h > 0 && cy + 1 > scroll + body_h {
                scroll = cy + 1 - body_h;
            }
        }
        scroll = scroll.min(col_h.saturating_sub(body_h.max(1)));
        self.import_scroll = Some(scroll);
        let view = import_body(&self.import, left_w, scroll, body_h, &slots);
        let tid = huebox_editor::cursor_id(&self.import);
        let name = tid
            .as_ref()
            .and_then(|id| self.import.display.get(id))
            .cloned()
            .unwrap_or_default();
        let preview = huebox_editor::cursor_slots(&mut self.import);
        let right = import_preview(&name, &preview, &slots, preview_w, body_h);
        let bg = huebox_render::slot_hex(&slots, "background").to_owned();
        let content: Vec<Row> = view
            .rows
            .into_iter()
            .zip(right)
            .map(|(mut left, right)| {
                left.extend(right);
                let mut row = huebox_render::layout::clip_row(&left, dlg_w);
                while huebox_render::layout::row_visible(&row) < dlg_w {
                    row.push(huebox_render::Cell::new(' ', "", &bg));
                }
                row
            })
            .collect();
        let mut lines = render_rows(&content, &slots);
        // The list column's scrollbar rides over the content's last two
        // cells — transparent track, thumb only.
        for (line, thumb) in lines
            .iter_mut()
            .zip(import_scrollbar(col_h, body_h, scroll).iter())
        {
            if let Some(thumb) = thumb {
                apply_import_thumb(line, *thumb, left_w, &slots);
            }
        }
        lines.extend(bar);
        let (btn_line, confirm, cancel) = import_footer_buttons(dlg_w, &slots);
        lines.push(btn_line);
        let rect = popup_rect(
            Rect::new(0, 0, cols as u16, rows as u16),
            (dlg_w + 2) as u16,
            (lines.len() + 2) as u16,
        );
        let inner_w = rect.width.saturating_sub(2) as usize;
        lines.truncate(rect.height.saturating_sub(2) as usize);
        while lines.len() < rect.height.saturating_sub(2) as usize {
            lines.push(blank_line(inner_w, &slots));
        }
        let by = rect.y as usize + 1 + lines.len().saturating_sub(1);
        let confirm_btn = Some(Rect::new(
            (rect.x as usize + 1 + confirm.0) as u16,
            by as u16,
            confirm.1.saturating_sub(confirm.0) as u16,
            1,
        ));
        let cancel_btn = Some(Rect::new(
            (rect.x as usize + 1 + cancel.0) as u16,
            by as u16,
            cancel.1.saturating_sub(cancel.0) as u16,
            1,
        ));
        self.popup_lines = pad_lines(lines, inner_w, &slots);
        self.popup = Some(PopupLayout {
            kind: PopupKind::Import,
            ox: rect.x as usize,
            oy: rect.y as usize,
            w: inner_w,
            h: self.popup_lines.len(),
            hits: vec![],
            import_ids: view.ids,
            import_groups: view.groups,
            confirm_btn,
            cancel_btn,
            bare: false,
        });
    }

    /// Cells → Ratatui: body, docked bar, dimmed base, dialog on top.
    pub fn render(&mut self, frame: &mut Frame) {
        let area = frame.area();
        if area.width as usize != self.cols || area.height as usize != self.rows {
            self.set_size(area.width, area.height);
            self.refresh();
        }
        let slots = self.state.slots.clone();
        let bar_h = self.foot_lines.len() as u16;
        let body_area = Rect::new(0, 0, area.width, area.height.saturating_sub(bar_h));
        let bar_area = Rect::new(0, area.height.saturating_sub(bar_h), area.width, bar_h);
        match self.body_mode {
            BodyMode::Bare => {
                frame.render_widget(Paragraph::new(self.body_lines.clone()), body_area);
            }
            BodyMode::Stacked => {
                self.render_stacked(frame, body_area, &slots);
            }
            BodyMode::Side => {
                self.render_side(frame, body_area, &slots);
            }
        }
        if bar_h > 0 {
            frame.render_widget(Paragraph::new(self.foot_lines.clone()), bar_area);
        }
        let Some(layout) = self.popup.clone() else {
            return;
        };
        dim_buffer(frame.buffer_mut(), area, &slots);
        // The too-small fallback owns its rows alone: no border chrome.
        if layout.bare {
            frame.render_widget(Paragraph::new(self.popup_lines.clone()), area);
            return;
        }
        let rect = Rect::new(
            layout.ox as u16,
            layout.oy as u16,
            (layout.w + 2) as u16,
            (self.popup_lines.len() + 2) as u16,
        );
        frame.render_widget(dialog_block(&slots), rect);
        let inner = Rect::new(
            rect.x + 1,
            rect.y + 1,
            rect.width.saturating_sub(2),
            rect.height.saturating_sub(2),
        );
        frame.render_widget(Paragraph::new(self.popup_lines.clone()), inner);
    }

    /// Stacked body: top row, then bordered `THEME` / `EXAMPLES` panels.
    fn render_stacked(&self, frame: &mut Frame, body_area: Rect, slots: &Slots) {
        // Base fill first: past the layout maxima the extra stays the
        // buffer's own background, never the terminal's.
        let bg = hex_color(slot_hex(slots, "background"));
        frame.render_widget(
            Paragraph::new(vec![]).style(ratatui::style::Style::new().bg(bg)),
            body_area,
        );
        if let Some(top) = &self.top {
            self.render_top(frame, top, slots);
        } else if let Some(stacked) = &self.stacked {
            if !stacked.header.is_empty() {
                let h = stacked.header.len() as u16;
                frame.render_widget(
                    Paragraph::new(stacked.header.clone()),
                    Rect::new(body_area.x, body_area.y, body_area.width, h),
                );
            }
        }
        let Some(stacked) = &self.stacked else {
            return;
        };
        if !stacked.controls.is_empty() {
            let block = panel_block(CONTROLS_TITLE, slots);
            let inner = stacked
                .controls_rect
                .inner(ratatui::layout::Margin::new(1, 1));
            frame.render_widget(block, stacked.controls_rect);
            frame.render_widget(Paragraph::new(stacked.controls.clone()), inner);
        }
        if !stacked.examples.is_empty() {
            let block = panel_block(EXAMPLES_TITLE, slots);
            let inner = stacked
                .examples_rect
                .inner(ratatui::layout::Margin::new(1, 1));
            frame.render_widget(block, stacked.examples_rect);
            frame.render_widget(Paragraph::new(stacked.examples.clone()), inner);
        }
    }

    /// Side body: top row, then the pair in bordered panels.
    fn render_side(&self, frame: &mut Frame, body_area: Rect, slots: &Slots) {
        let bg = hex_color(slot_hex(slots, "background"));
        frame.render_widget(
            Paragraph::new(vec![]).style(ratatui::style::Style::new().bg(bg)),
            body_area,
        );
        if let Some(top) = &self.top {
            self.render_top(frame, top, slots);
        } else if let Some(side) = &self.side {
            if !side.chrome.is_empty() {
                let h = side.chrome.len() as u16;
                frame.render_widget(
                    Paragraph::new(side.chrome.clone()),
                    Rect::new(body_area.x, body_area.y, body_area.width, h),
                );
            }
        }
        let Some(side) = &self.side else {
            return;
        };
        let left_block = panel_block(CONTROLS_TITLE, slots);
        let left_inner = side.left_rect.inner(ratatui::layout::Margin::new(1, 1));
        frame.render_widget(left_block, side.left_rect);
        frame.render_widget(Paragraph::new(side.left.clone()), left_inner);
        let right_block = panel_block(EXAMPLES_TITLE, slots);
        let right_inner = side.right_rect.inner(ratatui::layout::Margin::new(1, 1));
        frame.render_widget(right_block, side.right_rect);
        frame.render_widget(Paragraph::new(side.right.clone()), right_inner);
    }

    /// Compositor top: bare logo + info column beside `EDITOR`.
    fn render_top(&self, frame: &mut Frame, top: &TopCache, slots: &Slots) {
        let logo_rect = Rect::new(
            top.logo_x as u16,
            0,
            top.left_w as u16,
            top.top_screen as u16,
        );
        let info_rect = Rect::new(
            top.info_x as u16,
            0,
            top.info_w as u16,
            top.top_screen as u16,
        );
        let editor_rect = Rect::new(
            top.editor_x as u16,
            0,
            top.editor_outer as u16,
            top.top_screen as u16,
        );
        frame.render_widget(Paragraph::new(top.logo.clone()), logo_rect);
        frame.render_widget(Paragraph::new(top.info.clone()), info_rect);
        let block = panel_block(EDITOR_TITLE, slots);
        let inner = editor_rect.inner(ratatui::layout::Margin::new(1, 1));
        frame.render_widget(block, editor_rect);
        frame.render_widget(Paragraph::new(top.editor_inner.clone()), inner);
    }

    /// Render into a `TestBackend` buffer: the headless frame test's paint.
    ///
    /// No real tty anywhere near it — geometry in, buffer out.
    pub fn snapshot(&mut self, cols: u16, rows: u16) -> ratatui::buffer::Buffer {
        use ratatui::backend::TestBackend;
        use ratatui::Terminal;
        self.set_size(cols, rows);
        self.refresh();
        let backend = TestBackend::new(cols, rows);
        let mut terminal = Terminal::new(backend).expect("test terminal");
        terminal
            .draw(|frame| self.render(frame))
            .expect("test draw");
        terminal.backend().buffer().clone()
    }
}

/// Whether the padded lay keeps everything the unpadded one showed.
///
/// Decision 45: the same top shape, the same blocks, no block fewer rows.
/// Narrower folds may show *more* rows for the same widgets.
fn pad_keeps_content(base: &StackedLay, airy: &StackedLay) -> bool {
    let (_, _, ref_regions, _, ref_top, ref_head, _) = base;
    let (_, _, cand_regions, _, cand_top, cand_head, _) = airy;
    if (cand_top != cand_head) != (ref_top != ref_head) {
        return false;
    }
    let ref_counts: std::collections::HashMap<&str, usize> = ref_regions
        .iter()
        .map(|(name, _, count)| (name.as_str(), *count))
        .collect();
    let cand_counts: std::collections::HashMap<&str, usize> = cand_regions
        .iter()
        .map(|(name, _, count)| (name.as_str(), *count))
        .collect();
    if ref_counts.keys().collect::<std::collections::HashSet<_>>()
        != cand_counts.keys().collect::<std::collections::HashSet<_>>()
    {
        return false;
    }
    ref_counts
        .iter()
        .all(|(name, count)| cand_counts.get(*name).copied().unwrap_or(0) >= *count)
}

/// One row plus `pad` blank cells on each side (panel air, decision 45).
fn with_side_air(mut row: Row, pad: usize, slots: &Slots) -> Row {
    if pad == 0 {
        return row;
    }
    let fill = slot_hex(slots, "background").to_owned();
    let mut out = Vec::with_capacity(row.len() + 2 * pad);
    for _ in 0..pad {
        out.push(Cell::new(' ', "", &fill));
    }
    out.append(&mut row);
    for _ in 0..pad {
        out.push(Cell::new(' ', "", &fill));
    }
    out
}

/// One info-column button row: air + selection-pair button + air.
///
/// `air` blank cells on each side (the column's own margin), the button
/// `btn_w` wide centered on its label. The click map checks the button
/// span only; the air is chrome.
fn button_row_in(
    width: usize,
    air: usize,
    btn_w: usize,
    label: &str,
    slots: &Slots,
) -> Line<'static> {
    let bg = hex_color(slot_hex(slots, "background"));
    let btn = button_line(label, btn_w, slots);
    let mut spans = vec![];
    for _ in 0..air {
        spans.push(ratatui::text::Span::styled(
            " ",
            Style::new().fg(ratatui::style::Color::Reset).bg(bg),
        ));
    }
    // `button_line` already wears the selection pair; keep its spans so
    // tests find the label whole.
    spans.extend(btn.spans);
    while spans
        .iter()
        .map(|span: &ratatui::text::Span| span.content.chars().count())
        .sum::<usize>()
        < width
    {
        spans.push(ratatui::text::Span::styled(
            " ",
            Style::new().fg(ratatui::style::Color::Reset).bg(bg),
        ));
    }
    let mut line = Line::from(spans);
    line.style = Style::new().bg(bg);
    line
}

/// Pad every line to `cols` display columns in the buffer background.
///
/// The frame rows already stand on the fill per-cell; the pad covers the
/// columns past the last span so no column shows the terminal background.
fn pad_lines(lines: Vec<Line<'static>>, cols: usize, slots: &Slots) -> Vec<Line<'static>> {
    let bg = hex_color(huebox_render::slot_hex(slots, "background"));
    lines
        .into_iter()
        .map(|mut line| {
            line.style = Style::new().bg(bg);
            let mut width = line.width();
            let mut spans = line.spans;
            while width < cols {
                spans.push(ratatui::text::Span::styled(
                    " ",
                    Style::new().fg(ratatui::style::Color::Reset).bg(bg),
                ));
                width += 1;
            }
            let mut padded = Line::from(spans);
            padded.style = Style::new().bg(bg);
            padded
        })
        .collect()
}

/// Below the floor: the centred hint, fully unstyled, over blank
/// buffer-background fill — never the bar, never a dialog pad.
fn small_lines(cols: usize, rows: usize, slots: &Slots) -> Vec<Line<'static>> {
    use huebox_render::floor::too_small_frame;
    let hint: String = too_small_frame(cols).iter().map(|cell| cell.ch).collect();
    let mut lines = vec![Line::from(vec![ratatui::text::Span::styled(
        hint,
        Style::new()
            .fg(ratatui::style::Color::Reset)
            .bg(ratatui::style::Color::Reset),
    )])];
    while lines.len() < rows {
        lines.push(blank_line(cols, slots));
    }
    lines.truncate(rows);
    lines
}

/// One blank fill line for dialog padding.
fn blank_line(cols: usize, slots: &Slots) -> Line<'static> {
    let bg = hex_color(huebox_render::slot_hex(slots, "background"));
    let mut line = Line::from(vec![ratatui::text::Span::styled(
        " ".repeat(cols.max(1)),
        Style::new().fg(ratatui::style::Color::Reset).bg(bg),
    )]);
    line.style = Style::new().bg(bg);
    line
}

/// Mouse capture lives outside `run`'s init/restore, so a `Drop` guard
/// owns the disable: panics unwind through it and the terminal comes
/// back without the pointer stuck in capture mode.
struct MouseCapture;

impl MouseCapture {
    fn enable() -> Self {
        use ratatui::crossterm::event::EnableMouseCapture;
        use ratatui::crossterm::execute;
        let _ = execute!(std::io::stdout(), EnableMouseCapture);
        MouseCapture
    }
}

impl Drop for MouseCapture {
    fn drop(&mut self) {
        use ratatui::crossterm::event::DisableMouseCapture;
        use ratatui::crossterm::execute;
        let _ = execute!(std::io::stdout(), DisableMouseCapture);
    }
}

/// One event against the shell; `true` breaks the loop.
fn handle_event(shell: &mut Shell, event: Event) -> std::io::Result<bool> {
    match event {
        Event::Key(key) if matches!(key.kind, KeyEventKind::Press | KeyEventKind::Repeat) => {
            Ok(route_key(shell, crate::keys::translate(&key)) == Outcome::Quit)
        }
        Event::Key(_) => Ok(false), // Release: ignored, held keys keep stepping
        Event::Mouse(mouse) => {
            match mouse.kind {
                MouseEventKind::Down(MouseButton::Left) => {
                    let _ =
                        crate::mouse::click_at(shell, mouse.column as usize, mouse.row as usize);
                }
                MouseEventKind::ScrollUp => {
                    let _ = crate::mouse::wheel(shell, -1);
                }
                MouseEventKind::ScrollDown => {
                    let _ = crate::mouse::wheel(shell, 1);
                }
                _ => {}
            }
            Ok(shell.state.quit)
        }
        Event::Resize(cols, rows) => {
            shell.set_size(cols, rows);
            Ok(false)
        }
        _ => Ok(false),
    }
}

fn run_loop(terminal: &mut DefaultTerminal, mut shell: Shell) -> std::io::Result<Shell> {
    let _mouse = MouseCapture::enable();
    terminal.draw(|frame| shell.render(frame))?;
    loop {
        // Block for the first key; a held key arrives as Press + Repeats.
        let first = event::read()?;
        let key_start = Instant::now();
        let mut quit = handle_event(&mut shell, first)?;
        // Coalesce: apply the rest of the burst, paint once.
        while event::poll(Duration::ZERO)? {
            quit |= handle_event(&mut shell, event::read()?)?;
        }
        if quit || shell.state.quit {
            break;
        }
        let key_ms = key_start.elapsed().as_secs_f64() * 1000.0;
        let frame_start = Instant::now();
        shell.refresh();
        let frame_ms = frame_start.elapsed().as_secs_f64() * 1000.0;
        let draw_start = Instant::now();
        terminal.draw(|frame| shell.render(frame))?;
        let draw_ms = draw_start.elapsed().as_secs_f64() * 1000.0;
        debug::stages(key_ms, frame_ms, draw_ms);
    }
    Ok(shell)
}

/// Run one session to completion: init, loop, restore.
///
/// `ratatui::run` initialises (raw mode, alternate screen) and restores
/// on exit, panic included — the bricked-terminal class of bug cannot
/// recur through this entry point.
pub fn run(shell: Shell) -> std::io::Result<Shell> {
    ratatui::run(|terminal| run_loop(terminal, shell))
}
