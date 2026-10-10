# huebox 004 — Rust research

Checked 2026-10-10. Pins versions, proves each risky mapping with a code
example, and records the parity traps. Backs `plan.md`; ships no product code.

Pinned: `ratatui 0.30`, `crossterm 0.29`, `syntect 5`, `clap 4`,
`unicode-width 0.2`, `serde 1` + `toml`. No `cargo` on this machine yet —
toolchain install is plan Phase 0 (`yay -S rustup`, then stable).

## 1. Depend on the backend once

Since 0.27 Ratatui re-exports its backends. Depending on `ratatui` plus a
separate `crossterm` risks two crossterm versions in one tree (event types
then mismatch by crate version and nothing compiles). One dependency:

```toml
[dependencies]
ratatui = { version = "0.30", features = ["crossterm"] }
```

```rust
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
```

## 2. Event loop: blocking read, coalesced redraw

The app is event-driven (no animation): apply every key to state (cheap),
redraw once. `request_redraw`'s coalescing ports as drain-pending-then-draw.
`Repeat` counts as a press (held keys must keep stepping); `Release` is
ignored. `poll` with a zero timeout drains without blocking:

```rust
use ratatui::crossterm::event::{self, Event, KeyEventKind};
use std::time::Duration;

ratatui::run(|mut terminal| {
    let mut app = App::new();
    loop {
        // Block for the first key; a held key arrives as Press then Repeats.
        let Event::Key(key) = event::read()? else { continue };
        if !matches!(key.kind, KeyEventKind::Press | KeyEventKind::Repeat) {
            continue;
        }
        app.apply_key(&translate(&key));
        // Coalesce: apply the rest of the burst, paint once.
        while event::poll(Duration::ZERO)? {
            if let Event::Key(k) = event::read()? {
                if matches!(k.kind, KeyEventKind::Press | KeyEventKind::Repeat) {
                    app.apply_key(&translate(&k));
                }
            }
        }
        if app.quit { break Ok(()); }
        terminal.draw(|frame| app.render(frame))?;
    }
})
```

`ratatui::run` initialises (raw mode, alternate screen) and restores on exit,
panic included — the bricked-terminal class of bug cannot recur through this
entry point. The two prompts need the terminal back mid-run (same seam as
`App.suspend()`): use `try_init`/`try_restore` manually there, or
`terminal.backend_mut()` with crossterm `disable_raw_mode` +
`LeaveAlternateScreen`, read the line on the real stdout, then re-enter.
Prompt text flows through the same injected `prompt_hex`/`prompt_name` seams,
so headless tests never touch a tty.

## 3. Key translation: one table, tested

crossterm reports structured keys; the state machine keeps its own names
(`esc`, `\x03`, arrows, `enter`). Port `app.py`'s `KEYS` as one function —
the only place the two vocabularies meet — and table-test it:

```rust
fn translate(key: &KeyEvent) -> &'static str {
    use KeyCode::*;
    if key.modifiers.contains(KeyModifiers::CONTROL) {
        if let Char(c) = key.code {
            return match c {
                'c' => "\x03", 's' => "\x13",
                'a' => "\x01", 'd' => "\x04",
                _ => "?",
            };
        }
    }
    match key.code {
        Esc => "esc",
        Enter => "\r",
        Left => "left", Right => "right",
        Up => "up", Down => "down",
        Char(c) => Box::leak(c.to_string().into_boxed_str()),
        _ => "?",
    }
}
```

Note: plain `c`/`s`/`a`/`d` are colour keys — only the Ctrl-held forms map
to control codes. Unknown keys answer `"?"` (a no-op in `apply_key`), never
panic. (`Box::leak` above is illustrative; product code interns single chars
without leaking — e.g. a `CompactString` or a precomputed table.)

## 4. Cells, not ANSI: the round-trip that caused the lag

Python builds ANSI strings, re-parses them per row (`Text.from_ansi`, 4.4ms),
then renders. Ratatui builds styled cells directly — `Span::styled(text,
Style::new().fg(rgb).bg(rgb))`, `Line::from(spans)`, `frame.render_widget` —
so the 4.4ms stage and its cache cease to exist. There is no `from_ansi`
anywhere in the rewrite; a reviewer finding one fails the phase.

```rust
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};

fn hex(s: &str) -> Color {
    let v = u32::from_str_radix(s.trim_start_matches('#'), 16).unwrap();
    Color::Rgb((v >> 16) as u8, (v >> 8) as u8, v as u8)
}

fn chrome<'a>(text: &'a str, slot: &str, slots: &Slots) -> Span<'a> {
    Span::styled(text, Style::new().fg(hex(&slots[slot])))
}

// Backdrop (§8.2): fill the row to the width in the buffer's own background.
fn backdrop<'a>(spans: Vec<Span<'a>>, slots: &Slots, cols: usize) -> Line<'a> {
    let mut line = Line::from(spans);
    line.style = Style::new().bg(hex(&slots["background"]));
    // pad with background-coloured air to `cols` display columns
    line.width(); // unicode-width, see §7
    line
}
```

Bold maps to `Style::new().bold()`. No DIM anywhere (§8.1 binds).

## 5. Mouse: hits model ports unchanged

Enable with `ratatui::crossterm::event::EnableMouseCapture` (and disable on
exit — `run` does not do this automatically). A click resolves through the
same row-announced `hits` plus `slot_at`, then goes through `apply_key`:

```rust
Event::Mouse(m) => match m.kind {
    MouseEventKind::Down(MouseButton::Left) => {
        if let Some(slot) = slot_at(&hits, m.column as usize, m.row as usize) {
            app.click_slot(slot); // selection + apply_key path, never direct
        }
    }
    MouseEventKind::ScrollUp => app.apply_key("up"),
    MouseEventKind::ScrollDown => app.apply_key("down"),
    _ => {}
},
```

Coordinates are already frame-relative; dialog origins recompute live exactly
as the popup screens do now.

## 6. Sample highlighting: hand tokenizer, not syntect

The sample text is FIXED (nine lines). syntect's default set has no Zig
grammar (it ships Sublime defaults; Zig would ride in via an extra
`.sublime-syntax` compiled at build time), and any grammar risks token-stream
drift from Pygments. Recommendation: tokenize the fixed lines with a ~60-line
purpose-built splitter whose output is table-tested against Pygments'
captured stream (part of the parity fixture), mapping to the existing
`TOKEN_SLOTS` table. If the sample text ever changes, the table test fails
and forces the splitter update — same guard, no grammar dependency.

syntect stays the fallback if the sample becomes user content. For reference,
the shape that would take:

```rust
use syntect::easy::HighlightLines;
use syntect::parsing::SyntaxSet;

// load once at startup; `Zig.sublime-syntax` embedded at build time
let ps = SyntaxSet::load_defaults_newlines();
let syntax = ps.find_syntax_by_extension("zig").unwrap();
let mut h = HighlightLines::new(syntax, &ThemeSet::load_defaults().themes["base16-ocean.dark"]);
let ranges: Vec<(Style, &str)> = h.highlight_line(line, &ps).unwrap();
// map each syntect Style.foreground -> nearest TOKEN_SLOTS entry
```

## 7. Colour maths: port colorsys verbatim, mind the rounding trap

Python's `colorsys` is the spec, not any Rust colour crate (their hue
definitions differ). The algorithms, from the local stdlib:

```rust
pub fn rgb_to_hsv(r: f64, g: f64, b: f64) -> (f64, f64, f64) {
    let maxc = r.max(g).max(b);
    let minc = r.min(g).min(b);
    let rangec = maxc - minc;
    let v = maxc;
    if minc == maxc {
        return (0.0, 0.0, v);
    }
    let s = rangec / maxc;
    let rc = (maxc - r) / rangec;
    let gc = (maxc - g) / rangec;
    let bc = (maxc - b) / rangec;
    let h = if r == maxc {
        bc - gc
    } else if g == maxc {
        2.0 + rc - bc
    } else {
        4.0 + gc - rc
    };
    ((h / 6.0) % 1.0, s, v)
}

pub fn hsv_to_rgb(h: f64, s: f64, v: f64) -> (f64, f64, f64) {
    if s == 0.0 {
        return (v, v, v);
    }
    let i = (h * 6.0) as i64; // truncates toward zero, like int()
    let f = h * 6.0 - i as f64;
    let p = v * (1.0 - s);
    let q = v * (1.0 - s * f);
    let t = v * (1.0 - s * (1.0 - f));
    match ((i % 6) + 6) % 6 {
        0 => (v, t, p),
        1 => (q, v, p),
        2 => (p, v, t),
        3 => (p, q, v),
        4 => (t, p, v),
        _ => (v, p, q),
    }
}
```

TRAP — `rgb_to_hex` uses `int(round(c))`, and Python `round()` is
half-to-EVEN while Rust `f64::round()` is half-AWAY. `127.5` agrees (128),
`2.5` does not (Python 2, Rust 3). Port banker's rounding explicitly:

```rust
fn round_half_even(x: f64) -> f64 {
    let f = x.floor();
    let d = x - f;
    if d < 0.5 {
        f
    } else if d > 0.5 {
        f + 1.0
    } else if f % 2.0 == 0.0 {
        f
    } else {
        f + 1.0
    }
}

pub fn rgb_to_hex(r: f64, g: f64, b: f64) -> String {
    let c = |x: f64| x.clamp(0.0, 255.0);
    format!("#{:02x}{:02x}{:02x}",
        round_half_even(c(r)) as u8,
        round_half_even(c(g)) as u8,
        round_half_even(c(b)) as u8)
}
```

`step_hsv` keeps `HUE_STEP = 1/360`, `CHANNEL_STEP = 0.02`, hue wraps with
float modulo, sat/val clamp. `%` on negative `f64` in Rust keeps the sign
(unlike Python) — normalise with `((x % 1.0) + 1.0) % 1.0`. Width measurement
uses `unicode-width`; its tables drift from CPython's `unicodedata` across
versions, so `clip`/`visible` carry a property test on adversarial strings
(wide glyphs, combining marks, escapes) rather than a hand-checked table.

## 8. Layout and popups

`grid_geometry`, the panel/side/bare ladder, `MIN_COLS`×`MIN_ROWS`, and the
dialog-margin origins port as pure functions with the Python layout tests
ported beside them. Popups are stacked Ratatui areas over a dimmed base
(clear + dim style, never an opaque fill); one input router owns the surface
per screen, mirroring `on_key`'s guards; modals never stack. Footer is a
one-line bottom area fed by the same hint pairs; dialogs fold it to two lines
with status last, same rule as `_fold_footer`.
