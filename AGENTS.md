# Project Overview

huebox is a terminal theme editor with live preview. It shows the colours a
terminal is actually using and lets users change them from a keyboard-driven
interface — no hex codes, no hand-editing, no restarting.

- Python ≥ 3.9; runtime dependencies are Pygments (the code sample) and Textual (the editor), everything else is stdlib
- Supported terminals: Ghostty, kitty, Alacritty
- `~/.config/huebox` is the truth (one file per theme plus `state.toml`); a terminal config is a push target, not where a theme lives
- Keep it single-purpose: colour slots in, colour slots out. Not a config editor, not a theme store.

# Code Guidelines

Binding code, architecture, and design rules live in
[code-guidelines.md](docs/dev/code-guidelines.md).

**You MUST read [code-guidelines.md](docs/dev/code-guidelines.md) before writing
or modifying any code, or doing any architecture or design work on this project.**

Summary (details in the linked document):
- **Modular Design** — one job per module, minimal cross-module deps, no cycles
- **Facade Pattern** — `FORMATS` registry, `themes.save`/`push`, `detect.resolve()`; callers never reach into format internals
- **Dependency Injection** — `editor` reaches the world only through injected callables `cli` builds; never import `themes`/`detect` into `editor`; `cli` is the composition root
- **State/View Separation** — `EditorState` + `apply_key` own all behaviour, `app` is a thin shell over `render`'s rows, buffer renders every frame and disk writes on Ctrl+S only
- **Coding Notes** — line-level config writes, `with open(...)`, 3.9-compatible code, `huebox: ` errors to stderr with exit 1, tests mirror modules and stay clean under `-W always`

# Response Style

Always be brief. Lead with the answer, then supporting detail. Structure sentences as `[thing] [action] [reason]. [next step].` (e.g. "Bug in auth middleware. Token expiry check use `<` not `<=`. Fix:"). Skip filler, hedging, and pleasantries ("sure", "certainly", "basically"). No tool-call narration, no decorative tables or emoji; quote only the shortest decisive line of an error rather than dumping full logs. Code blocks, commands, and error strings stay verbatim.

When a decision is needed from the user, ask structured: state the question, list the concrete options with a one-line trade-off each, and give a recommendation — never an open-ended question where concrete options would do.

# Shell Commands

```sh
python3 -m unittest discover -s tests
python3 -W always -m unittest discover -s tests   # before calling a phase done
```

Always put a timer (`timeout` / `-timeout`) on `find` and similar long-running commands.
