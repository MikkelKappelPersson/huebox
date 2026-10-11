# Contributing

Setup with Python ≥ 3.9:

```sh
uv sync --extra test
```

Fallback without `uv`:

```sh
pip install -e .[test]
```

Rust (the in-progress port under `rust/`): stable toolchain via rustup, then

```sh
cargo build --release --manifest-path rust/Cargo.toml   # single binary at rust/target/release/huebox
cargo install --path rust/huebox-cli                    # or install `huebox` from this checkout
```

The pty closure harness (`tests/test_pty_rust.py`) drives that release binary, so build it before running the suite. The Python tree stays the reference until the harness and the live-redraw bench both gate green.

Read `docs/dev/code-guidelines.md` before touching code. It is binding.

Keep scope: colour slots in, colour slots out. Not a config editor, not a theme store.

## Tests

```sh
uv run python -m unittest discover -s tests
uv run python -W always -m unittest discover -s tests   # required, must be clean
```

Mirror the module under test (`tests/test_<module>.py`). New behaviour needs a case; a new format needs round-trip plus byte-identical no-op cases.

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
