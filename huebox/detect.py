"""Finding the terminal you are in and the config holding its colours (§7).

Finding a config is only half of it: `ensure_theme_pointer` writes the one
line that decides *which* file a Ghostty config reads (§13.6), and does so
with the same line-level discipline every other write to somebody else's
file follows (§6.2).
"""

from __future__ import annotations

import os
import re
import shutil

from .formats import FORMAT_NAMES, FORMATS


def _config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def _prefer_xdg(paths: list[str]) -> list[str]:
    """Put the XDG location first so tests and sandboxes are respected."""
    out: list[str] = []
    base = _config_home()
    for path in paths:
        if path.startswith("~/.config/"):
            out.append(os.path.join(base, path[len("~/.config/"):]))
        out.append(path)
    return out


for _spec in FORMATS.values():
    _spec["defaults"] = _prefer_xdg(_spec["defaults"])

FORMATS["kitty"]["defaults"] = [os.path.join(_config_home(), "kitty/kitty.conf")] \
    + FORMATS["kitty"]["defaults"]


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def _probes():
    """Terminals to try, most trustworthy first.

    Per-window variables beat TERM_PROGRAM, because launchers and
    multiplexers leak stale values into the environment.
    """
    return [
        ("ghostty", lambda: os.environ.get("GHOSTTY_RESOURCES_DIR")),
        ("kitty", lambda: os.environ.get("KITTY_WINDOW_ID")
         or os.environ.get("KITTY_LISTEN_ON")),
        ("alacritty", lambda: os.environ.get("ALACRITTY_WINDOW_ID")
         or os.environ.get("ALACRITTY_SOCKET")),
        ("wezterm", lambda: os.environ.get("WEZTERM_EXECUTABLE")
         or os.environ.get("WEZTERM_PANE")),
    ]


def detect_order() -> list[str]:
    names = [name for name, probe in _probes() if probe()]
    program = os.environ.get("TERM_PROGRAM", "").lower()
    names += [name for name in FORMAT_NAMES if name in program and name not in names]
    names += [name for name in FORMAT_NAMES if name not in names]
    return names


def _candidate_paths(fmt: str):
    spec = FORMATS[fmt]
    for var, filename in spec["env"].items():
        value = os.environ.get(var)
        if not value:
            continue
        base = value if os.path.isdir(value) else os.path.dirname(value) or "."
        candidate = os.path.join(base, filename) if filename else value
        if os.path.isfile(candidate):
            yield candidate
    for candidate in spec["defaults"]:
        expanded = os.path.expanduser(candidate)
        if os.path.isfile(expanded):
            yield expanded


#: `config-file = ?path`, read with the same discipline as THEME_LINE
#: below: the value is quoted or bare, a bare one runs to the first `#`,
#: and trailing spacing is not part of it. v1 took the rest of the line,
#: so `config-file = ~/x.conf  # mine` pointed at a file called
#: `~/x.conf  # mine` and the include went unfound (§7.4).
CONFIG_FILE_LINE = re.compile(
    r"^\s*config-file\s*=\s*"
    r"(?:(?P<q>[\"'])(?P<path>[^\"']*)(?P=q)|(?P<path_bare>[^#]*?\S|))"
    r"\s*(?:#.*)?$")


def _include_value(match) -> str:
    """The value a CONFIG_FILE_LINE match points at, quotes stripped."""
    return match.group("path") or match.group("path_bare") or ""


def _ghostty_includes(config_path: str) -> list[str]:
    """Config files pulled in via `config-file = ?path`.

    Read with the rule `THEME_LINE` below states for `theme =` (§7.4): a
    trailing comment is a comment, `?path` is relative to this config, and
    an include that is not on disk is skipped rather than reported.
    """
    out = []
    directory = os.path.dirname(os.path.abspath(config_path))
    try:
        with open(config_path, encoding="utf-8",
                  errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return out
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = CONFIG_FILE_LINE.match(line)
        if not match:
            continue
        target = _include_value(match).replace("?", directory + os.sep)
        target = target.replace("~", os.path.expanduser("~"))
        if os.path.isfile(target):
            out.append(target)
    return out


#: `theme = Name`, split so the value can be read (and written) without
#: touching the spacing, the quoting or a trailing comment (§6.2). The
#: value may be quoted (any characters but the quote) or bare — bare
#: values run to the first `#` and cannot end in a space, which is how
#: Ghostty itself reads them; this is what makes space-named built-ins
#: (`theme = Catppuccin Mocha`) followable. The last match in a file
#: wins, which is how Ghostty reads one too.
THEME_LINE = re.compile(
    r"^(?P<pre>\s*theme\s*=\s*)"
    r"(?:(?P<q>[\"'])(?P<name>[^\"']*)(?P=q)|(?P<bare>[^#]*?\S|))"
    r"(?P<post>[ \t]*(?:#.*)?)(?P<cr>\r?)$")


def _theme_value(match) -> str:
    """The value a THEME_LINE match points at, quotes stripped."""
    if match.group("q"):
        return match.group("name")
    return match.group("bare") or ""


def ghostty_themes_dir() -> str:
    """`$XDG_CONFIG_HOME/ghostty/themes` — where `theme = Name` resolves.

    Read at call time, not at import time, so a test's temporary home is
    respected (the candidate-path tables are fixed when the module loads).
    """
    return os.path.join(_config_home(), "ghostty", "themes")


def ghostty_main_config(path: str = None) -> str:
    """The Ghostty config itself, with `theme =` and includes *not* followed.

    `_resolve_for` answers "which file holds the colours", which is the
    wrong question for an export: it writes its own theme file and needs
    the config that decides which file is read. That is the first
    candidate path that exists — or the explicit `--config` the user named,
    which is the file they mean by definition.
    """
    if path:
        expanded = os.path.expanduser(path)
        return expanded if os.path.isfile(expanded) else None
    for candidate in _candidate_paths("ghostty"):
        return candidate              # the first one there is, colours or not
    return None


def ghostty_theme_name(config_path: str):
    """The theme `config_path`'s last `theme =` line names, or `None`.

    The name a config is *on*, with no opinion about whether the file
    behind it exists — that is the whole question `push` asks to decide
    where a save belongs (§13.6). A config with no such line, or one
    whose line is commented out, is on no theme and the answer is `None`.
    """
    name = None
    try:
        with open(config_path, encoding="utf-8",
                  errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = THEME_LINE.match(line)
        if match:
            name = _theme_value(match)
    return name or None


def _ghostty_theme_file(config_path: str):
    """Follow `theme = Name` to the file that actually holds the colours.

    Ghostty keeps colours in a separate theme file far more often than
    inline, so the main config on its own usually has nothing to edit.
    The value is read with the same rule `ensure_theme_pointer` writes
    with, so a trailing comment cannot make the two disagree about which
    theme a config is on.
    """
    name = ghostty_theme_name(config_path)
    if not name:
        return None
    user = os.path.join(ghostty_themes_dir(), name)
    if os.path.isfile(user):
        return user
    resources = os.environ.get("GHOSTTY_RESOURCES_DIR", "/usr/share/ghostty")
    shipped = os.path.join(resources, "themes", name)
    if os.path.isfile(shipped):
        # a read-only shipped theme: copy somewhere we are allowed to edit
        os.makedirs(os.path.dirname(user), exist_ok=True)
        shutil.copy2(shipped, user)
        return user
    return None


def ensure_theme_pointer(config_path: str, name: str) -> str:
    """Point `config_path` at the Ghostty theme file `name` (§13.6).

    A Ghostty theme file is a full config of its own (plan appendix A), so
    the theme huebox exports is picked up by the `theme =` line in the main
    config. This is that one line, and only that one line:

    - an existing `theme =` keeps its spacing, its quotes and its trailing
      comment, and only the value is swapped;
    - a config with no theme line gets one appended, and nothing else in
      the file moves;
    - a config already pointing at `name` is not rewritten at all, so its
      mtime survives a no-op save.

    Returns `unchanged`, `rewritten` or `appended`, which is what the push
    report says. `name` is the caller's to validate — `export_ghostty_native`
    has already put a `valid_name` one on disk; the guard here is only
    against a value that would break the file into two lines or two keys.
    """
    if not name or any(char in name for char in "\"'#\r\n"):
        raise ValueError(f"not a usable theme name: {name!r}")
    # newline="" on both ends: universal-newline translation would rewrite
    # a CRLF config on the way in, and surrogateescape carries a config
    # that is not valid UTF-8 through untouched. §6.2 promises byte
    # equality for every line we do not touch, and that is this open mode.
    with open(config_path, encoding="utf-8", errors="surrogateescape",
              newline="") as handle:
        text = handle.read()

    # split on "\n" rather than splitlines: the trailing "" carries the
    # final newline, so re-joining reproduces every byte we did not change
    lines = text.split("\n")
    index = None
    for position, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if THEME_LINE.match(line):
            index = position                 # a later key wins, as in §7.4
    if index is not None:
        match = THEME_LINE.match(lines[index])
        if _theme_value(match) == name:
            return "unchanged"
        # keep the original quoting: a quoted pointer stays quoted, a bare
        # one stays bare — only the value is swapped (§6.2)
        quote = match.group("q") or ""
        lines[index] = (f"{match.group('pre')}{quote}{name}{quote}"
                        f"{match.group('post')}{match.group('cr')}")
        action = "rewritten"
    else:
        # keep the file's own line ending: split("\n") leaves the "\r" on
        # the line before the trailing ""
        ending = "\r" if "\r\n" in text else ""
        line = f"theme = {name}{ending}"
        if lines[-1:] == [""]:
            lines[-1:-1] = [line]       # the final "" is the newline: keep it
        else:
            lines.append(line)          # the file ended without one
        action = "appended"

    # the lines are joined back with the "\n" they were split on, so every
    # byte we did not choose to change is written back as it came
    with open(config_path, "w", encoding="utf-8", errors="surrogateescape",
              newline="") as handle:
        handle.write("\n".join(lines))
    return action


def config_holds_colours(fmt: str, path: str) -> bool:
    """Does `path` define colours — directly or through what it pulls in?

    §7.3 refuses to offer a terminal whose config has no colours, and
    §7.4 says which file the colours really live in. This is both of those
    questions for one *named* path: the main Ghostty config is allowed to
    hold no colours of its own when it points at a theme file, and that
    config is still a real terminal config.
    """
    read = FORMATS[fmt]["read"]
    if read(path):
        return True
    if fmt == "ghostty":
        for included in _ghostty_includes(path):
            if read(included):
                return True
        themed = _ghostty_theme_file(path)
        if themed and read(themed):
            return True
    return False


def infer_format(path: str):
    """Which format a `--config` path belongs to (§7.1).

    A known file name wins — `kitty.conf`, `alacritty.toml`,
    `config.ghostty` are unambiguous. For a bare `config` or anything we
    have no name for, the file decides: whichever reader finds colours in
    it. A tie (or an empty file) is not inferable, and the caller says so.
    """
    name = os.path.basename(path).lower()
    for fmt in FORMAT_NAMES:
        for candidate in FORMATS[fmt]["defaults"]:
            if os.path.basename(candidate).lower() == name:
                return fmt
    counts = [(len(FORMATS[fmt]["read"](path)), fmt) for fmt in FORMAT_NAMES]
    best = max(count for count, _ in counts)
    if best == 0:
        return None
    winners = [fmt for count, fmt in counts if count == best]
    return winners[0] if len(winners) == 1 else None


def _resolve_for(fmt: str):
    """First config for `fmt` that actually defines colours."""
    read = FORMATS[fmt]["read"]
    for candidate in _candidate_paths(fmt):
        if fmt == "ghostty":
            for included in _ghostty_includes(candidate):
                if read(included):
                    return included
            themed = _ghostty_theme_file(candidate)
            if themed:
                candidate = themed
        if read(candidate):
            return candidate
    return None


def resolve(fmt: str | None = None, path: str | None = None):
    """Return (format, config_path, error)."""
    if fmt and fmt not in FORMATS:
        return None, None, f"unknown format {fmt!r}"
    if path:
        expanded = os.path.expanduser(path)
        if not os.path.isfile(expanded):
            return fmt, None, f"no such file: {expanded}"
        return fmt or infer_format(expanded), expanded, None

    order = [fmt] if fmt else detect_order()
    for name in order:
        found = _resolve_for(name)
        if found:
            return name, found, None
    if fmt:
        return fmt, None, f"no {fmt} config with colours found"
    return None, None, ("found no terminal config with colours; "
                        "pass --format with one of "
                        f"{', '.join(FORMAT_NAMES)}")
