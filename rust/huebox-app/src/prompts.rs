//! The two prompts: one line, with the terminal handed over for it.
//!
//! Ports `app.py`'s `prompt_text`: `apply_key` calls
//! `prompt_hex`/`prompt_name` synchronously, so the answer must be ready
//! when the call returns. The production closures restore the terminal to
//! what it was before the app started (cooked mode, no alternate screen
//! — exactly the drop-out-of-raw-mode pattern `edit()` used), read the
//! line on the real stdout, then re-enter — which keeps the seam
//! synchronous instead of turning the whole key surface async.
//!
//! Headless-testable by construction: [`read_prompt_line`] is pure over
//! injected buffers (tests pin EOF, errors and trimming there — a tty is
//! never touched in tests), and the editor seams stay plain [`PromptFn`]
//! closures, so router tests hand in fakes returning canned strings.

use std::io::{BufRead, Write};

use huebox_editor::PromptFn;

use crate::debug;

/// One line of input over injected buffers: the prompt pattern, shared.
///
/// Writes `label`, flushes, reads a line. `None` on EOF or error
/// (cancelled: the caller leaves buffer and library exactly as they
/// were); otherwise the trimmed line — empty included, which the caller
/// reads as a cancel of its own.
pub fn read_prompt_line(
    label: &str,
    input: &mut dyn BufRead,
    output: &mut dyn Write,
) -> Option<String> {
    let _ = write!(output, "{label}");
    let _ = output.flush();
    // Byte loop over `fill_buf`, not `read_line`: `read_line` retries
    // `Interrupted` internally, so a SIGINT against the prompt handler
    // would spin forever instead of cancelling. Here every error —
    // Ctrl+C included — is `None`.
    let mut line = Vec::new();
    loop {
        match input.fill_buf() {
            Ok([]) => break, // EOF
            Ok(buf) => {
                if let Some(pos) = buf.iter().position(|&b| b == b'\n') {
                    line.extend_from_slice(&buf[..=pos]);
                    input.consume(pos + 1);
                    break;
                }
                let len = buf.len();
                line.extend_from_slice(buf);
                input.consume(len);
            }
            Err(_) => return None,
        }
    }
    if line.is_empty() {
        return None;
    }
    match String::from_utf8(line) {
        Ok(s) => Some(s.trim().to_owned()),
        Err(_) => None,
    }
}

/// Hand the terminal back, read the line, re-enter.
///
/// Raw mode off, alternate screen left, mouse capture dropped — then the
/// reverse. Best-effort throughout: a terminal that will not suspend
/// still gets its prompt attempt, and the event loop repaints everything
/// on return.
fn suspend_terminal() {
    use ratatui::crossterm::event::DisableMouseCapture;
    use ratatui::crossterm::execute;
    use ratatui::crossterm::terminal::{disable_raw_mode, LeaveAlternateScreen};
    let _ = disable_raw_mode();
    let _ = execute!(std::io::stdout(), LeaveAlternateScreen, DisableMouseCapture);
}

/// Re-enter after [`suspend_terminal`].
fn resume_terminal() {
    use ratatui::crossterm::event::EnableMouseCapture;
    use ratatui::crossterm::execute;
    use ratatui::crossterm::terminal::{enable_raw_mode, EnterAlternateScreen};
    let _ = enable_raw_mode();
    let _ = execute!(std::io::stdout(), EnterAlternateScreen, EnableMouseCapture);
}

/// The production prompter: suspend, read on the real stdio, re-enter.
///
/// One constructor for both seams — `prompt_hex` and `prompt_name` share
/// the pattern, as in the editor. Ctrl+C or EOF cancels the prompt and
/// returns to the editor rather than ending it (001 §4.3): a SIGINT
/// handler is registered around the cooked read so the blocking
/// `read_line` fails with `Interrupted` instead of killing the process,
/// and [`read_prompt_line`] already maps every error to `None`. The
/// handler is unregistered before re-entering raw mode (where ISIG is
/// off and Ctrl+C arrives as the `\x03` byte through the normal router).
pub fn suspending_prompter() -> PromptFn {
    Box::new(move |label: &str| {
        debug::prompt(label);
        suspend_terminal();
        let _guard = InterruptCancel::register();
        let answer = read_prompt_line(label, &mut std::io::stdin().lock(), &mut std::io::stdout());
        drop(_guard);
        resume_terminal();
        answer
    })
}

/// A SIGINT handler that turns Ctrl+C during a suspend-prompt into an
/// interrupted read (→ `None` → back to the editor) instead of process
/// death. Registers on construction, unregisters on drop, so the
/// disposition never leaks past the prompt.
struct InterruptCancel {
    id: Option<signal_hook::SigId>,
    // Held so the flag handler has a live target for the prompt's life.
    _flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl InterruptCancel {
    fn register() -> Self {
        let flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        // Safe handler: all it does is set the flag, which nothing reads —
        // its presence alone turns SIGINT from process death into an
        // interrupted read. Unregistered on drop.
        let id = signal_hook::flag::register(signal_hook::consts::SIGINT, flag.clone()).ok();
        Self { id, _flag: flag }
    }
}

impl Drop for InterruptCancel {
    fn drop(&mut self) {
        if let Some(id) = self.id {
            signal_hook::low_level::unregister(id);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn read(label: &str, bytes: &[u8]) -> (Option<String>, String) {
        let mut input = Cursor::new(bytes);
        let mut output = Vec::new();
        let answer = read_prompt_line(label, &mut input, &mut output);
        (answer, String::from_utf8(output).expect("prompt bytes"))
    }

    #[test]
    fn line_is_labeled_trimmed_and_kept() {
        let (answer, shown) = read("  new hex for palette-5: ", b"  #ff8800  \n");
        assert_eq!(answer.as_deref(), Some("#ff8800"));
        assert_eq!(shown, "  new hex for palette-5: ");
    }

    #[test]
    fn eof_cancels() {
        let (answer, _) = read("  new theme name: ", b"");
        assert_eq!(answer, None);
    }

    #[test]
    fn empty_line_is_empty_not_cancelled() {
        // The caller decides what "" means (`ask` cancels, hex ignores);
        // the seam reports it honestly.
        let (answer, _) = read("  new theme name: ", b"\n");
        assert_eq!(answer.as_deref(), Some(""));
    }

    #[test]
    fn interrupted_read_cancels_like_ctrl_c() {
        // SIGINT against a registered handler surfaces as
        // `ErrorKind::Interrupted` — the prompt's Ctrl+C shape.
        struct Interrupted;
        impl std::io::Read for Interrupted {
            fn read(&mut self, _buf: &mut [u8]) -> std::io::Result<usize> {
                Err(std::io::Error::new(
                    std::io::ErrorKind::Interrupted,
                    "sigint",
                ))
            }
        }
        let mut input = std::io::BufReader::new(Interrupted);
        let mut output = Vec::new();
        assert_eq!(read_prompt_line("label: ", &mut input, &mut output), None);
    }

    #[test]
    fn broken_input_cancels() {
        struct Broken;
        impl std::io::Read for Broken {
            fn read(&mut self, _buf: &mut [u8]) -> std::io::Result<usize> {
                Err(std::io::Error::other("gone"))
            }
        }
        let mut input = std::io::BufReader::new(Broken);
        let mut output = Vec::new();
        assert_eq!(read_prompt_line("label: ", &mut input, &mut output), None);
    }
}
