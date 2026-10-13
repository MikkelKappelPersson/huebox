# 005 Onboarding — plan

Phase 0 — bundled themes plus seam. Add `rust/src/bundled/` sources, `library::bundled::{list, read}`, golden round-trip tests. Gate: three themes parse to 22 slots.

Phase 1 — editor no-theme state. Add the overlay state, bundled-picker cursor, `apply_key` transitions (create, import-pending, abort-returns, close-quits-when-empty). Gate: headless unit cases green.

Phase 2 — app popup. Add `PopupKind::NoTheme` rows and bundled list rows over the dimmed editor. Gate: pty reaches the dialog from an empty home.

Phase 3 — ensure-target plus cli wiring. Add the `EnsureTargetFn` seam, missing-file creation, pointer append, bundled group in the import seam. Gate: create-then-save holds colours on all three terminals.

Phase 4 — gates. Full `cargo test`, clippy `-D warnings`, `cargo fmt --check`, pty harness. Gate: all green, then version bump.
