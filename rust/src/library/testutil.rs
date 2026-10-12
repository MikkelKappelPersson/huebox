//! Test-only helpers: serialised process env and isolated temp dirs.
//!
//! `unittest` runs serially; `cargo test` runs threads in one process.
//! Every test that touches the process environment takes [`lock_env`],
//! which restores `unittest`'s serial semantics, and works inside an
//! isolated `XDG_CONFIG_HOME` via [`temp_dir`]. No third-party crates.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

/// Process-wide serialiser for environment-mutating tests.
static ENV_LOCK: Mutex<()> = Mutex::new(());

/// Env vars this crate's code reads. The guard clears them all, applies
/// `vars`, and restores the previous values on drop.
const MANAGED: &[&str] = &[
    "XDG_CONFIG_HOME",
    "HOME",
    "GHOSTTY_RESOURCES_DIR",
    "KITTY_WINDOW_ID",
    "KITTY_LISTEN_ON",
    "KITTY_PID",
    "KITTY_CONFIG_DIRECTORY",
    "KITTY_CONFIG_DIR",
    "ALACRITTY_WINDOW_ID",
    "ALACRITTY_SOCKET",
    "WEZTERM_EXECUTABLE",
    "WEZTERM_PANE",
    "TERM_PROGRAM",
];

/// Lock the process env and install `vars` (all other managed vars
/// cleared). Hold the guard for the whole test.
///
/// The serialiser is a plain mutex: never hold two guards on one thread.
/// A shadowing `let _guard` does NOT drop the first guard — `drop` it
/// explicitly before re-locking, or the test deadlocks.
pub fn lock_env(vars: &[(&str, &str)]) -> EnvGuard {
    let guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let mut saved: Vec<(String, Option<String>)> = Vec::new();
    for name in MANAGED {
        saved.push(((*name).to_owned(), std::env::var(*name).ok()));
        std::env::remove_var(name);
    }
    for (name, value) in vars {
        std::env::set_var(name, value);
    }
    EnvGuard {
        _lock: guard,
        saved,
    }
}

/// Restores the managed env on drop.
pub struct EnvGuard {
    _lock: MutexGuard<'static, ()>,
    saved: Vec<(String, Option<String>)>,
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        for (name, value) in self.saved.drain(..) {
            match value {
                Some(value) => std::env::set_var(&name, &value),
                None => std::env::remove_var(&name),
            }
        }
    }
}

static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// An isolated scratch directory, removed on drop (best effort).
pub struct TestDir {
    path: PathBuf,
}

impl TestDir {
    /// Root string for `XDG_CONFIG_HOME` / `HOME`.
    pub fn root(&self) -> String {
        self.path.to_string_lossy().into_owned()
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn path_buf(&self) -> PathBuf {
        self.path.clone()
    }

    pub fn write(&self, relative: &str, text: &str) -> PathBuf {
        let path = self.path.join(relative);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).expect("test tempdir");
        }
        std::fs::write(&path, text).expect("test write");
        path
    }

    pub fn read(&self, path: &Path) -> String {
        std::fs::read_to_string(path).expect("test read")
    }
}

impl Drop for TestDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// A fresh scratch directory (`huebox-lib-test-<pid>-<n>`).
pub fn temp_dir(tag: &str) -> TestDir {
    let n = COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let path = std::env::temp_dir().join(format!(
        "huebox-lib-test-{}-{}-{}",
        std::process::id(),
        tag,
        n
    ));
    std::fs::create_dir_all(&path).expect("test tempdir");
    TestDir { path }
}
