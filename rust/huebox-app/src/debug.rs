//! The profiling seam: `HUEBOX_DEBUG` / `HUEBOX_DEBUG_LOG`.
//!
//! Ports `app.py`'s `_debug` from the first UI commit (spec §7 binds):
//! when `HUEBOX_DEBUG` is set, every line goes to stderr AND, when
//! `HUEBOX_DEBUG_LOG` names a path, is appended to that file — so the pty
//! bench reads the same shapes from either shell and compares
//! apples-to-apples. Logging never fails a frame: file errors are
//! dropped, like a debug print.
//!
//! Line shapes (the first three are byte-identical to `app.py`):
//!
//! * `huebox.app: redraw 100x24: 22 rows, 7 blocks, sel=5, widest=100`
//! * `huebox.app: resize to 100x24`
//! * `huebox.app: prompt '  new hex for palette-5: '`
//! * `huebox.app: stages key=0.006ms frame=1.430ms draw=0.210ms`
//!
//! The `stages` line is the Rust half of the §6 budget table: `key` is
//! `apply_key`, `frame` is layout plus cell rows, `draw` is the Ratatui
//! diff plus write.

use std::io::Write as _;

/// Whether any debug line is emitted.
pub fn enabled() -> bool {
    std::env::var_os("HUEBOX_DEBUG").is_some()
}

/// The append path, when `HUEBOX_DEBUG_LOG` names one.
pub fn log_path() -> Option<String> {
    std::env::var("HUEBOX_DEBUG_LOG")
        .ok()
        .filter(|path| !path.is_empty())
}

/// One line, to stderr and the log file when enabled.
pub fn emit(message: &str) {
    if !enabled() {
        return;
    }
    let line = format!("huebox.app: {message}");
    eprintln!("{line}");
    if let Some(path) = log_path() {
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
        {
            let _ = writeln!(file, "{line}");
        }
    }
}

/// The frame that just painted — same shape as `app.py`.
pub fn redraw(cols: usize, rows: usize, nrows: usize, blocks: usize, sel: usize, widest: usize) {
    emit(&format!(
        "redraw {cols}x{rows}: {nrows} rows, {blocks} blocks, sel={sel}, widest={widest}"
    ));
}

/// A resize the shell applied — same shape as `app.py`.
pub fn resized(size: &str) {
    emit(&format!("resize to {size}"));
}

/// A prompt about to take the terminal — same shape as `app.py` (`%r`).
pub fn prompt(label: &str) {
    emit(&format!("prompt '{label}'"));
}

/// Per-stage timings for one event-loop turn.
pub fn stages(key_ms: f64, frame_ms: f64, draw_ms: f64) {
    emit(&format!(
        "stages key={key_ms:.3}ms frame={frame_ms:.3}ms draw={draw_ms:.3}ms"
    ));
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, MutexGuard};

    /// `std::env` is process-global and tests run in threads: serialize
    /// every test that touches it, or parallel runs flake each other.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn lock_env() -> MutexGuard<'static, ()> {
        ENV_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    struct EnvGuard {
        debug: Option<String>,
        log: Option<String>,
    }

    impl EnvGuard {
        fn take() -> Self {
            Self {
                debug: std::env::var("HUEBOX_DEBUG").ok(),
                log: std::env::var("HUEBOX_DEBUG_LOG").ok(),
            }
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            match &self.debug {
                Some(value) => std::env::set_var("HUEBOX_DEBUG", value),
                None => std::env::remove_var("HUEBOX_DEBUG"),
            }
            match &self.log {
                Some(value) => std::env::set_var("HUEBOX_DEBUG_LOG", value),
                None => std::env::remove_var("HUEBOX_DEBUG_LOG"),
            }
        }
    }

    #[test]
    fn line_shapes_match_app_py() {
        let _lock = lock_env();
        let _guard = EnvGuard::take();
        let dir = std::env::temp_dir().join(format!("huebox-app-debug-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("debug.log");
        let _ = std::fs::remove_file(&path);
        std::env::set_var("HUEBOX_DEBUG", "1");
        std::env::set_var("HUEBOX_DEBUG_LOG", &path);

        redraw(100, 24, 22, 7, 5, 100);
        resized("100x24");
        prompt("  new hex for palette-5: ");
        stages(0.006, 1.43, 0.21);

        let logged = std::fs::read_to_string(&path).expect("log file");
        let lines: Vec<&str> = logged.lines().collect();
        assert_eq!(
            lines,
            [
                "huebox.app: redraw 100x24: 22 rows, 7 blocks, sel=5, widest=100",
                "huebox.app: resize to 100x24",
                "huebox.app: prompt '  new hex for palette-5: '",
                "huebox.app: stages key=0.006ms frame=1.430ms draw=0.210ms",
            ]
        );
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn silent_unless_enabled() {
        let _lock = lock_env();
        let _guard = EnvGuard::take();
        std::env::remove_var("HUEBOX_DEBUG");
        std::env::remove_var("HUEBOX_DEBUG_LOG");
        assert!(!enabled());
        assert_eq!(log_path(), None);
        // Must not panic, print, or create anything.
        redraw(80, 24, 1, 1, 0, 80);
        stages(0.0, 0.0, 0.0);
    }
}
