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

use std::time::{Duration, Instant};

use huebox_core::Slots;
use huebox_editor::import::ThemeId;
use huebox_editor::{
    head_label, overrides_lines, session_path, setup_lines, theme_lines, EditorState, ImportState,
    ImportWriter,
};
use huebox_render::frame::{self, editor_hints, FrameOpts, Hit};
use huebox_render::grid::grid_geometry;
use huebox_render::layout::row_visible;
use huebox_render::Row;
use ratatui::crossterm::event::{self, Event, KeyEventKind, MouseButton, MouseEventKind};
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::Line;
use ratatui::widgets::Paragraph;
use ratatui::{DefaultTerminal, Frame};

use crate::cells::{hex_color, render_rows};
use crate::debug;
use crate::dialog::{
    clamp_width, dialog_block, dim_block, import_body, overrides_pairs, popup_rect, setup_pairs,
    themes_pairs, IMPORT_DIALOG_MAX_H, IMPORT_DIALOG_W, IMPORT_HINTS, OVERRIDES_DIALOG_W,
    SETUP_DIALOG_W, THEMES_DIALOG_MAX_H, THEMES_DIALOG_W,
};
use crate::footer::{bar_lines, fold_footer};
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
}

/// One editor session under the compositor.
pub struct Shell {
    /// The session: buffer, picker, choice, warning — all behaviour.
    pub state: EditorState,
    /// The import popup's cursor and selection.
    pub import: ImportState,
    /// Route keys to the import popup while open.
    pub import_open: bool,
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

    /// The footer pairs for the screen on top: one source, two paints.
    fn footer_pairs(&self) -> Vec<(String, String)> {
        // Owned: the editor source builds a fresh `Vec` per frame, so no
        // branch can hand out borrows into a temporary.
        let editor = editor_hints(self.state.undo.len());
        let pairs: Vec<(&str, &str)> = match self.screen() {
            Screen::Editor => editor
                .iter()
                .map(|(key, what)| (key.as_str(), what.as_str()))
                .collect(),
            Screen::Setup => setup_pairs(self.state.setup_name.is_some()).to_vec(),
            Screen::Themes => themes_pairs().to_vec(),
            Screen::Overrides => overrides_pairs().to_vec(),
            Screen::Import => IMPORT_HINTS.to_vec(),
        };
        pairs
            .into_iter()
            .map(|(key, what)| (key.to_owned(), what.to_owned()))
            .collect()
    }

    /// State → cells: grid, body frame, footer bar, dialog layout.
    pub fn refresh(&mut self) {
        let cols = self.cols;
        let rows = self.rows;
        self.state.grid = grid_geometry(cols, "  ");
        let slots = self.state.slots.clone();

        let pairs = self.footer_pairs();
        let pair_refs: Vec<(&str, &str)> = pairs
            .iter()
            .map(|(key, what)| (key.as_str(), what.as_str()))
            .collect();
        let status = self.state.status.clone();
        self.foot_lines = bar_lines(&pair_refs, &status, cols, &slots);
        let body_rows = rows.saturating_sub(self.foot_lines.len());

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
        self.widest = out
            .rows
            .iter()
            .map(|row| row_visible(row))
            .max()
            .unwrap_or(0);
        self.blocks = out.regions.len();
        self.hits = out.hits;
        let mut body = pad_lines(render_rows(&out.rows, &slots), cols, &slots);
        // The frame may return fewer rows than the area holds: the rest
        // is buffer-background fill, never the terminal's background.
        while body.len() < body_rows {
            body.push(blank_line(cols, &slots));
        }
        self.body_lines = body;

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
                let pairs: Vec<(&str, &str)> = IMPORT_HINTS.to_vec();
                let dlg_w = clamp_width(IMPORT_DIALOG_W, cols);
                let budget = rows
                    .saturating_sub(2 + bar_height(&pairs))
                    .clamp(1, IMPORT_DIALOG_MAX_H);
                let view = import_body(&mut self.import, dlg_w, budget, &status, &slots);
                (PopupKind::Import, dlg_w, view.rows, vec![], view.ids, pairs)
            }
        };
        let bar = bar_lines(&pairs, &status, dlg_w, &slots);
        let mut lines = render_rows(&content, &slots);
        lines.extend(bar);
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
        self.popup_lines = pad_lines(lines, inner_w, &slots);
        self.popup = Some(PopupLayout {
            kind,
            ox: rect.x as usize,
            oy: rect.y as usize,
            w: inner_w,
            h: self.popup_lines.len(),
            hits,
            import_ids: ids,
        });
    }

    /// Cells → Ratatui: body, docked bar, dimmed base, dialog on top.
    pub fn render(&mut self, frame: &mut Frame) {
        let area = frame.area();
        if area.width as usize != self.cols || area.height as usize != self.rows {
            self.set_size(area.width, area.height);
            self.refresh();
        }
        let bar_h = self.foot_lines.len() as u16;
        let body_area = Rect::new(0, 0, area.width, area.height.saturating_sub(bar_h));
        let bar_area = Rect::new(0, area.height.saturating_sub(bar_h), area.width, bar_h);
        frame.render_widget(Paragraph::new(self.body_lines.clone()), body_area);
        if bar_h > 0 {
            frame.render_widget(Paragraph::new(self.foot_lines.clone()), bar_area);
        }
        let Some(layout) = self.popup.clone() else {
            return;
        };
        let slots = self.state.slots.clone();
        frame.render_widget(dim_block(&slots), area);
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
                spans.push(ratatui::text::Span::styled(" ", Style::new().bg(bg)));
                width += 1;
            }
            let mut padded = Line::from(spans);
            padded.style = Style::new().bg(bg);
            padded
        })
        .collect()
}

/// One blank fill line for dialog padding.
fn blank_line(cols: usize, slots: &Slots) -> Line<'static> {
    let bg = hex_color(huebox_render::slot_hex(slots, "background"));
    let mut line = Line::from(vec![ratatui::text::Span::styled(
        " ".repeat(cols.max(1)),
        Style::new().bg(bg),
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
