# Project Overview

huebox is a terminal theme editor with live preview. It shows the colours a
terminal is actually using and lets users change them from a keyboard-driven
interface — no hex codes, no hand-editing, no restarting.

- Rust stable; single static binary, no runtime dependencies
- Supported terminals: Ghostty, kitty, Alacritty
- `~/.config/huebox` is the truth (one file per theme plus `state.toml`); a terminal config is a push target, not where a theme lives
- Keep it single-purpose: colour slots in, colour slots out. Not a config editor, not a theme store.

# Code Guidelines

Binding code, architecture, and design rules live in
[code-guidelines.md](docs/dev/code-guidelines.md).

**You MUST read [code-guidelines.md](docs/dev/code-guidelines.md) before writing
or modifying any code, or doing any architecture or design work on this project.**

Summary (details in the linked document):
- **Modular Design** — one job per crate, minimal cross-crate deps, no cycles
- **Facade Pattern** — `format::lookup`/`read`/`write`, `themes::{save, push}`, `detect::resolve()`; callers never reach into format internals
- **Dependency Injection** — `editor` reaches the world only through injected seams (`WriteFn`/`ApplyFn`/`DeleteFn`, `Library`, `ImportLibrary`) `cli` builds; the import popup follows the same rule; `cli` is the composition root
- **State/View Separation** — `EditorState` + `apply_key` own all behaviour, `app` is a thin shell over `render`'s rows, buffer renders every frame and disk writes on Ctrl+S only
- **Coding Notes** — line-level config writes, `huebox: ` errors to stderr with exit 1, tests mirror crates and stay clean under clippy `-D warnings` + `cargo fmt`

# Response Style

Always be brief. Lead with the answer, then supporting detail. Structure sentences as `[thing] [action] [reason]. [next step].` (e.g. "Bug in auth middleware. Token expiry check use `<` not `<=`. Fix:"). Skip filler, hedging, and pleasantries ("sure", "certainly", "basically"). No tool-call narration, no decorative tables or emoji; quote only the shortest decisive line of an error rather than dumping full logs. Code blocks, commands, and error strings stay verbatim.

When a decision is needed from the user, ask structured: state the question, list the concrete options with a one-line trade-off each, and give a recommendation — never an open-ended question where concrete options would do.

# Shell Commands

```sh
cargo test --workspace
cargo clippy --workspace -- -D warnings && (cd rust && cargo fmt --check)   # before calling a phase done
.venv/bin/python -m unittest discover -s tests   # pty harness, needs the release binary
```

Always put a timer (`timeout` / `-timeout`) on `find` and similar long-running commands.
