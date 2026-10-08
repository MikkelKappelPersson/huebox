# 🎨 huebox

A terminal theme editor with live preview — repaint your terminal without leaving it.

`huebox` shows you the colours your terminal is actually using, and lets you change them from a keyboard-driven interface — no hex codes, no hand-editing, no restarting.

## ✨ Two modes

### 🖥️ TUI editor

Just run `huebox` — that is the editor. It opens what you are working on and pushes on every save. Pick a slot with the arrows, nudge it into place, `Ctrl+S` and your terminal changes — no hex codes, no hand-editing, no restarting. Details in [Editing](#editing) below.

### ⌨️ Terminal commands

Everything else runs inline and exits — for scripts and one-shots:

```
  huebox --dump [name]  print the resolved colours and exit
  huebox --formats    list supported formats
  huebox new <name>   create a theme from your terminal (or a built-in ramp)
  huebox list         list your themes, current one marked
  huebox use <name>   make a theme current and push it to your terminal
  huebox import <name>  snapshot the detected terminal into a theme
```

Modifiers: by default a save pushes every terminal holding colours; `--to ghostty,kitty` narrows the push targets, `--no-push` writes the theme file only, `--no-reload` skips the terminal reload, `--ghostty-in-place` edits a Ghostty config instead of exporting a theme, `--force` overwrites on `new` / `import`, `--from <fmt>` and `--config` / `--format` override detection.

## 📦 Install

```sh
uv tool install huebox   # or: pip install --user huebox
```

The command lands in `~/.local/bin`. If your shell cannot find `huebox` afterwards, add that directory to your `PATH` (e.g. at the end of `~/.bashrc` or `~/.zshrc`) and reload the shell:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

To use it in the terminal you already have open, reload the config instead of restarting it:

```sh
source ~/.bashrc
```

Beyond the stdlib it needs [Pygments](https://pygments.org) (the code sample) and [Textual](https://textual.textualize.io/) (the editor), both installed automatically.

## 🎭 Themes

`~/.config/huebox` is the truth: one file per theme in `themes/<name>.toml`, plus `state.toml` naming the one you are working on. A terminal config is a push target, not the place your theme lives.

```sh
huebox import dusk            # snapshot this terminal into a theme
huebox                      # edit it; every Ctrl+S saves and pushes
huebox use dusk               # switch to it and push, no editor
huebox use dusk --to ghostty,kitty   # push to specific terminals
huebox use dusk --no-push             # switch without touching a config
```

`huebox` with no name opens the current theme (`t` inside switches); `huebox --dump <name>` reads a theme file instead of a config. With no theme library at all, `huebox` edits the terminal config in place.

## ✏️ Editing

Run `huebox` and drive it with the keyboard.

| Key | Action |
| --- | --- |
| arrows | move between slots |
| `w` / `e` | hue −/+ |
| `s` / `d` | saturation −/+ |
| `x` / `c` | lightness −/+ |
| `f` | cycle step size ×1 → ×5 → ×20 |
| `h` | type a hex value |
| `Ctrl+S` | save — the session's only write: the theme file, then a push |
| `u` / `r` | undo / revert to the last save |
| `t` | theme picker — arrows, `Enter` opens, `n` makes a theme from the buffer, `Esc` back |
| `i` / `I` | theme import — browse Ghostty/kitty/Alacritty themes, `space` toggles, `Enter` imports, `Esc` backs out |
| `N` | save the buffer as a new theme (and then save it) |
| `Esc` | quit — twice if there are unsaved changes |

The mouse works too: click a swatch to select it, a picker row to open it, wheel through long picker lists. Clicking anything else does nothing.

Edits live in an in-memory buffer — nothing is written until `Ctrl+S` — and the whole frame renders from that buffer, so palette, code sample and examples are live before the file changes (a tall frame also shows a git diff of the sample). The header names what you are editing (`ember ●` for a theme, `●` marks unsaved changes); `t` opens the picker without leaving the editor, and opening a theme with unsaved edits is refused rather than losing them. `Ctrl+S` writes the theme file first, then pushes to your terminal and asks it to re-read its config (`--no-reload` turns that off), so the colours are live when the command finishes.

A config is only ever edited line by line: a colour it does not define is reported (`not carried by this config: …`) and left alone — huebox never invents a line in your terminal's config.

### Ghostty themes

When your Ghostty config is organised by theme (a `theme =` line), a save writes the theme under *its own* name to `~/.config/ghostty/themes/` and moves that one line — saving `dusk` never rewrites another theme's file. With inline colours (or a `config-file` include) huebox edits the file in place instead. `--ghostty-native` forces the export, `--ghostty-in-place` forces the edit; a `theme =` line pointing at a missing file is repaired by writing it.

## 🖥️ Supported terminals

| Format | Config |
| --- | --- |
| Ghostty | `$XDG_CONFIG_HOME/ghostty/config.ghostty`, including `config-file` includes and `theme = Name` indirection |
| kitty | `$XDG_CONFIG_HOME/kitty/kitty.conf`, `~/.kitty.conf`, or `KITTY_CONFIG_DIRECTORY` |
| Alacritty | `$XDG_CONFIG_HOME/alacritty/alacritty.toml`, `$XDG_CONFIG_HOME/alacritty.toml`, `~/.alacritty.toml` (and the legacy `alacritty.yml`) |

Paths follow `XDG_CONFIG_HOME` when set; the search order is each terminal's own. huebox only offers a terminal whose config actually holds colours. Override detection with `--format`, or point at a file with `--config` (one push target only):

```sh
huebox --format kitty
huebox use ember --to ghostty --config ~/dotfiles/ghostty/config
```

## 🛡️ Safety

Writes are line-level: only the colour tokens are replaced, so comments, ordering, alignment and every unrelated setting survive untouched. A save that changes nothing does not write the file at all — its mtime is untouched too. The only file huebox writes outside your terminal config is its own `~/.config/huebox` library.

## 🛠️ Development

```sh
python3 -m unittest discover -s tests
```

## License

MIT
