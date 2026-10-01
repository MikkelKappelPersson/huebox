"""Finding the terminal you are in and the config holding its colours (§7)."""

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


def _ghostty_includes(config_path: str) -> list[str]:
    """Config files pulled in via `config-file = ?path`."""
    out = []
    directory = os.path.dirname(os.path.abspath(config_path))
    try:
        lines = open(config_path, encoding="utf-8",
                     errors="replace").read().splitlines()
    except OSError:
        return out
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"\s*config-file\s*=\s*(.+?)\s*$", line)
        if not match:
            continue
        target = match.group(1).strip().strip("\"'")
        target = (target.replace("?", directory + os.sep)
                        .replace("~", os.path.expanduser("~")))
        if os.path.isfile(target):
            out.append(target)
    return out


def _ghostty_theme_file(config_path: str):
    """Follow `theme = Name` to the file that actually holds the colours.

    Ghostty keeps colours in a separate theme file far more often than
    inline, so the main config on its own usually has nothing to edit.
    """
    name = None
    try:
        lines = open(config_path, encoding="utf-8",
                     errors="replace").read().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"\s*theme\s*=\s*(.+?)\s*$", line)
        if match:
            name = match.group(1).strip().strip("\"'")
    if not name:
        return None
    user = os.path.join(_config_home(), "ghostty", "themes", name)
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
