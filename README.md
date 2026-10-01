# huebox

A terminal theme editor with live preview.

`huebox` shows you the colours your terminal is actually using, and lets you
change them from a keyboard-driven interface — no hex codes, no hand-editing,
no restarting.

```
  huebox              TUI when stdout is a terminal, static preview otherwise
  huebox edit         force the interactive editor
  huebox show         force the static preview
  huebox --dump       print the resolved colours and exit
  huebox --formats    list supported formats
  huebox new <name>   create a theme from your terminal (or a built-in ramp)
  huebox list         list your themes, current one marked
  huebox use <name>   make a theme current and push it to your terminal
  huebox import <name>  snapshot the detected terminal into a theme
```

## Install

```sh
git clone https://github.com/MikkelKappelPersson/huebox
cd huebox
pipx install .          # or: pip install --user .
```

No third-party dependencies. [Pygments](https://pygments.org) is optional and
only improves the code sample in the editor:

```sh
pipx install '.[highlight]'
```

## Themes

`~/.config/huebox` is the truth: one file per theme in `themes/<name>.toml`,
plus `state.toml` naming the one you are working on. A terminal config is a
push target, not the place your theme lives.

```sh
huebox import dusk            # snapshot this terminal into a theme
huebox edit dusk              # edit it; every Ctrl+S saves and pushes
huebox use dusk               # switch to it and push, no editor
huebox use dusk --to ghostty,kitty   # push to specific terminals
huebox use dusk --no-push             # switch without touching a config
```

`huebox edit` with no name opens the current theme; `huebox show <name>` and
`huebox --dump <name>` read a theme file instead of a config. With no theme
library at all, `huebox edit` is what it always was: editing the terminal
config in place.

## Editing

Run `huebox edit` and drive it with the keyboard.

| Key | Action |
| --- | --- |
| arrows | move between slots |
| `q` / `w` | hue −/+ |
| `a` / `s` | saturation −/+ |
| `z` / `x` | lightness −/+ |
| `f` | cycle step size ×1 → ×5 → ×20 |
| `i` | type a hex value |
| `Ctrl+S` | save — the session's only write: the theme file, then a push |
| `u` / `r` | undo / revert to the last save |
| `t` | theme picker — arrows, `Enter` opens, `n` makes a theme from the buffer, `Esc` back |
| `N` | save the buffer as a new theme (and then save it) |
| `Esc` | quit — twice if there are unsaved changes |

Edits live in an in-memory buffer: nothing is written until you press
`Ctrl+S`. The editor *renders* from that buffer, so everything on screen —
palette, interface, code sample, and the background / selection / cursor
examples — is live and truecolor before the file changes. Quit with unsaved
changes and huebox asks for a second `Esc` first; `r` throws the buffer away
and goes back to your last save.

The header names what you are editing: `ember ● ghostty` for a theme (the `●`
marks unsaved buffer changes) or `direct:/path/to/config` in a legacy
direct-config session. `t` opens the theme picker without leaving the editor:
arrows and `Enter` to open a theme, `n` to make one from the buffer you are
looking at, `Esc` to go back. Opening a theme while the buffer has unsaved
edits is refused with `save (Ctrl+S) or revert (r) first` rather than losing
them. `N` is the way out of a direct-config session: it asks for a name, makes
the theme, makes it current, and saves it through the same pipeline. If a name
is already taken, huebox says so and waits for `y` (overwrite) or another name
— no modal, and nothing is written until you answer.

`Ctrl+S` is two writes: the theme file first, then a push into the terminal
config that holds your colours — the status bar says which
(`saved dusk → ghostty`). The theme file is the truth and is never rolled
back: if a push fails, the save still stands, huebox says why on stderr, and
the session ends with exit 1. Editing a terminal config directly (no themes
yet) takes a `<config>.huebox.bak` on its first save; theme files get none.

Reload your terminal — Ghostty `Ctrl+Shift+,`, kitty `Ctrl+Shift+F5`,
alacritty picks changes up automatically — and the new colours are live. The
code sample inside the editor is rendered in truecolor from the values you are
editing, so it updates *before* you reload.

A config is only ever edited line by line, so a colour the config does not
define is reported (`not carried by this config: cursor-text, …`) and left
alone — huebox will not invent a line in your terminal's config. A Ghostty
theme file is the one file huebox writes whole, and only because the user
asked for it with `--ghostty-native`.

### Ghostty themes

Ghostty keeps its colours in theme files, and a push can join it there
instead of editing whatever file the colours sit in today. Opt in with
`--ghostty-native`:

```sh
huebox use dusk --to ghostty --ghostty-native     # or edit dusk + Ctrl+S
```

That writes `~/.config/ghostty/themes/dusk` — 22 colours, in Ghostty's own
`palette = 0=#…` spelling, read back by huebox without drift — and points
your main config at it with a single `theme =` line: an existing one keeps
its spacing, its quotes and its comment and only the value changes, a config
without one gets the line appended, and every other byte of the file is left
exactly as it was. Your previous theme file is not touched. The themes
directory is shared with Ghostty's built-ins, so an export overwrites a
same-name file there (the report tells you when it did).

It is opt-in because that `theme =` line is a layout choice in *your* main
config, not a colour: without the flag huebox keeps updating the file your
colours already live in, which is the safer default. The flag applies to the
ghostty target only — `--to ghostty,kitty` exports for Ghostty and pushes
kitty the ordinary way — and it cannot be combined with `--no-push`. The
two modes also compose: after a native push, a plain push follows the
pointer and updates that theme file in place.

## Supported terminals

| Format | Config |
| --- | --- |
| Ghostty | `~/.config/ghostty/config.ghostty`, including `config-file` includes and `theme = Name` indirection |
| kitty | `~/.config/kitty/kitty.conf`, `~/.kitty.conf` |
| Alacritty | `~/.config/alacritty/alacritty.toml`, dotted keys, `[section]` tables and inline tables |

huebox only offers a terminal whose config actually contains colours, so a
leftover `ALACRITTY_SOCKET` from a session last week will not hijack your
Ghostty config — and it will not push into one either. Override the guess with
`--format`:

```sh
huebox edit --format kitty
huebox show --config ~/dotfiles/alacritty.toml
huebox use ember --to ghostty --config ~/dotfiles/ghostty/config
```

`--config` pushes one format only: with a single `--to` it names the file,
with several `--to` targets it is refused (ambiguity, not a guess).

## Safety

Writes are line-level: only the colour tokens are replaced, so comments,
ordering, alignment and every unrelated setting survive untouched. A no-op
write is byte-identical to the input, which the test suite asserts for every
format. Pushing a theme uses that same writer, so a push is no more invasive
than the v1 in-place edit. The only file huebox writes outside your terminal
config is its own `~/.config/huebox` library.

## Development

```sh
python3 -m unittest discover -s tests
```

The tests cover reading every slot, round-tripping without drift, changing
only the intended lines, pushing without inventing keys, and keeping the
layout inside narrow terminals.

## License

MIT
