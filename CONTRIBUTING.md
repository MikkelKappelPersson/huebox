# Contributing

Setup with a stable Rust toolchain (`yay -S rustup`, then `rustup toolchain install stable`):

```sh
cargo build --manifest-path rust/Cargo.toml   # single binary at rust/target/release/huebox
cargo install --path rust/huebox-cli          # or install `huebox` from this checkout
```

The pty harness (`tests/test_pty_rust.py`, frozen oracle plus `pyte` screens over the release binary) runs under the dev `.venv`, which already carries `pyte`:

```sh
cargo build --release --manifest-path rust/Cargo.toml   # the binary the harness drives
.venv/bin/python -m unittest discover -s tests
```

Read `docs/dev/code-guidelines.md` before touching code. It is binding.

Keep scope: colour slots in, colour slots out. Not a config editor, not a theme store.

## Tests

```sh
cargo test --workspace                        # required, must be green
cargo clippy --workspace -- -D warnings       # required, must be clean
cd rust && cargo fmt --check                  # required, must be clean
.venv/bin/python -m unittest discover -s tests   # pty replay + bench, needs the release binary
```

Mirror the crate under test (`rust/<crate>/tests/<name>.rs`). New behaviour needs a case; a new format needs round-trip plus byte-identical no-op cases.

## Commits

Format: `<type>: <short imperative summary>` (lowercase type, no scope).

```sh
feat: docked footer bar
fix: reload kitty with SIGUSR1, remote control fallback
docs: lead with huebox as the editor
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `spec`, `release`. One change per commit; behaviour change with no guideline update is incomplete.

## Pull requests

One topic per PR. Include what changed, why, and the test command output. A PR is done when tests are green under `-W always` and the guidelines still match the code.
