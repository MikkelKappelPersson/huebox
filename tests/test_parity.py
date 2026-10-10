"""Parity oracle for the Rust rewrite (004 spec section 7, plan Phase 1).

Golden vectors for every colour function plus one scripted session as a
key-by-key trace. The Rust state machine must replay both fixtures exactly;
this suite stays the oracle until it does.

Regen rule: fixtures regenerate ONLY via ``HUEBOX_REGENERATE=1``. The suite
fails if a fixture is missing and the regen variable is unset (never
silently regenerates).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`
sys.path.insert(0, _HERE)  # tests dir: `import session`

import session  # noqa: E402
from huebox import color, editor  # noqa: E402
from huebox.color import SLOTS  # noqa: E402

FIXTURE_DIR = os.path.join(_HERE, "fixtures")
COLOR_FIXTURE = os.path.join(FIXTURE_DIR, "parity_color.json")
SESSION_FIXTURE = os.path.join(FIXTURE_DIR, "parity_session.json")
FLOAT_TOL = 1e-9
SAVE = "\x13"

# --------------------------------------------------------------------------
# fixed inputs (the oracle's answer key is computed from these)
# --------------------------------------------------------------------------

HEX_TO_RGB_INPUTS = [
    "#000000",
    "#ffffff",
    "#ff0000",
    "#00ff00",
    "#0000ff",
    "#61afef",
    "ff0000",
    "#AbCdEf",
    "#8c8c8c",
    "#8d8d8d",
    "#101014",
    "#123456",
]

RGB_TO_HEX_INPUTS = [
    [255, 0, 0],
    [0, 0, 0],
    [255, 255, 255],
    [128, 128, 128],
    [2.5, 2.5, 2.5],
    [3.5, 3.5, 3.5],
    [0.5, 0.5, 0.5],
    [1.5, 1.5, 1.5],
    [254.5, 0, 0],
    [127.5, 127.5, 127.5],
    [-1, 300, 127.5],
    [-100, 1000, 0],
    [-0.5, 255.5, 128.0],
    [1.5, 2.5, 3.5],
    [254.5, 254.5, 254.5],
    [97.00761, 167.971305, 238.935],
]

RGB_TO_HSV_INPUTS = [
    [255, 0, 0],
    [0, 255, 0],
    [0, 0, 255],
    [0, 0, 0],
    [255, 255, 255],
    [128, 128, 128],
    [97, 175, 239],
    [16, 16, 20],
    [63, 122, 63],
]

HSV_TO_RGB_INPUTS = [
    [0.0, 0.0, 0.0],
    [0.0, 1.0, 1.0],
    [0.5, 1.0, 1.0],
    [0.0, 0.0, 1.0],
    [1.0, 1.0, 1.0],
    [0.3333333333333333, 1.0, 1.0],
    [0.6666666666666666, 1.0, 1.0],
    [0.5751173708920189, 0.5941422594142259, 0.9372549019607843],
    [0.9973856209150327, 1.0, 1.0],
]

LUMINANCE_INPUTS = [
    "#000000",
    "#ffffff",
    "#ff0000",
    "#00ff00",
    "#0000ff",
    "#8c8c8c",
    "#8d8d8d",
    "#7f7f7f",
    "#808080",
    "#8e8e8e",
    "#101014",
    "#61afef",
]

READABLE_FG_INPUTS = [
    "#000000",
    "#ffffff",
    "#ff0000",
    "#00ff00",
    "#0000ff",
    "#8c8c8c",
    "#8d8d8d",
    "#7f7f7f",
    "#808080",
    "#8e8e8e",
    "#101014",
    "#61afef",
]

IS_HEX_INPUTS = [
    "#000000",
    "000000",
    "#ABCDEF",
    "abcdef",
    "  #ABCDEF  ",
    "#ffffff ",
    "#abcdeg",
    "#12345",
    "#1234567",
    "",
    "#12345g",
    "not a colour",
]

NORMALIZE_HEX_INPUTS = [
    "  #ABCDEF  ",
    "abcdef",
    "ABCDEF",
    "#AbC123",
    "  ff0000  ",
    "#000000",
    "#AbCdEf",
]

STEP_HSV_INPUTS = [
    ("#ff0000", "h", 1, 1),
    ("#ff0000", "h", -1, 1),
    ("#ff0000", "h", -1, 5),
    ("#ff0000", "h", 1, 20),
    ("#ff0000", "h", -1, 20),
    ("#ff0004", "h", 1, 1),
    ("#808080", "s", -1, 1),
    ("#808080", "s", -1, 5),
    ("#ff0000", "s", 1, 20),
    ("#ff0000", "s", -1, 1),
    ("#ff0000", "s", 1, 1),
    ("#000000", "v", -1, 1),
    ("#ffffff", "v", 1, 1),
    ("#ffffff", "v", 1, 20),
    ("#000000", "v", -1, 20),
    ("#808080", "v", 1, 1),
    ("#808080", "v", -1, 1),
    ("#3f7a3f", "h", 1, 1),
    ("#3f7a3f", "h", -1, 1),
    ("#3f7a3f", "s", 1, 1),
    ("#3f7a3f", "s", -1, 1),
    ("#3f7a3f", "v", 1, 1),
    ("#3f7a3f", "v", -1, 1),
    ("#3f7a3f", "s", 1, 5),
    ("#3f7a3f", "h", 1, 5),
    ("#3f7a3f", "v", -1, 20),
]

SESSION_META = {
    "fmt": "ghostty",
    "theme": "parity",
    "path": "parity.toml",
    "size": [100, 24],
}

SESSION_INITIAL_SLOTS = {
    "palette-0": "#000000",
    "palette-1": "#ff0000",
    "palette-2": "#00ff00",
    "palette-3": "#0000ff",
    "palette-4": "#61afef",
    "palette-5": "#3f7a3f",
    "palette-6": "#e0c06c",
    "palette-7": "#ffffff",
    "palette-8": "#808080",
    "palette-9": "#8c8c8c",
    "palette-10": "#8d8d8d",
    "palette-11": "#123456",
    "palette-12": "#abcdef",
    "palette-13": "#fedcba",
    "palette-14": "#112233",
    "palette-15": "#445566",
    "background": "#101014",
    "foreground": "#e6e6ea",
    "cursor-color": "#ff00ff",
    "cursor-text": "#00ffff",
    "selection-background": "#202028",
    "selection-foreground": "#f0f0f0",
}

SESSION_KEYS = [
    "e",
    "w",
    "d",
    "s",
    "c",
    "x",
    "L",
    "H",
    "f",
    "e",
    "f",
    "f",
    "down",
    "up",
    "u",
    "r",
    "e",
    SAVE,
    "d",
    "r",
    "h",
    "X",
    SAVE,
]

SESSION_PROMPTS = ["#AbCdEf", "#123456"]


# --------------------------------------------------------------------------
# builders (regen path only)
# --------------------------------------------------------------------------

def _build_color_data() -> Dict[str, Any]:
    return {
        "meta": {
            "hue_step": color.HUE_STEP,
            "channel_step": color.CHANNEL_STEP,
            "missing": color.MISSING,
            "slots": list(SLOTS),
        },
        "hex_to_rgb": [
            {"input": value, "expected": list(color.hex_to_rgb(value))}
            for value in HEX_TO_RGB_INPUTS
        ],
        "rgb_to_hex": [
            {"input": list(value), "expected": color.rgb_to_hex(tuple(value))}
            for value in RGB_TO_HEX_INPUTS
        ],
        "rgb_to_hsv": [
            {"input": list(value), "expected": list(color.rgb_to_hsv(tuple(value)))}
            for value in RGB_TO_HSV_INPUTS
        ],
        "hsv_to_rgb": [
            {
                "input": list(value),
                "expected": list(color.hsv_to_rgb(value[0], value[1], value[2])),
            }
            for value in HSV_TO_RGB_INPUTS
        ],
        "luminance": [
            {"input": value, "expected": color.luminance(value)}
            for value in LUMINANCE_INPUTS
        ],
        "readable_fg": [
            {"input": value, "expected": color.readable_fg(value)}
            for value in READABLE_FG_INPUTS
        ],
        "is_hex": [
            {"input": value, "expected": color.is_hex(value)} for value in IS_HEX_INPUTS
        ],
        "normalize_hex": [
            {"input": value, "expected": color.normalize_hex(value)}
            for value in NORMALIZE_HEX_INPUTS
        ],
        "step_hsv": [
            {
                "value": value,
                "channel": channel,
                "direction": direction,
                "mult": mult,
                "expected": color.step_hsv(value, channel, direction, mult),
            }
            for value, channel, direction, mult in STEP_HSV_INPUTS
        ],
    }


def _capture_session(
    initial: Dict[str, str],
    keys: List[str],
    prompts: List[str],
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    writes: List[Dict[str, Any]] = []

    def write(theme: Any, path: Any, slots: Dict[str, str]) -> str:
        writes.append({"theme": theme, "path": path, "slots": dict(slots)})
        return "saved"

    answers = list(prompts)

    def prompt(label: str) -> Any:
        if answers:
            return answers.pop(0)
        return None

    draws: List[Dict[str, Any]] = []

    def draw(
        fmt: str,
        path: str,
        slots: Dict[str, str],
        sel: int,
        undo: Any,
        status: str,
        mult: int,
        head: Any = None,
        grid: Any = None,
    ) -> None:
        draws.append(
            {"slots": dict(slots), "sel": sel, "status": status, "mult": mult}
        )

    library = editor.Library(
        listing=lambda: ["parity"],
        loader=lambda name: None,
        creator=lambda name, slots, force: ("", "unexpected create"),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        state = session.drive(
            list(keys),
            meta["fmt"],
            meta["path"],
            dict(initial),
            write,
            theme=meta["theme"],
            library=library,
            size=tuple(meta["size"]),
            prompt=prompt,
            draw=draw,
        )
    trace: List[Dict[str, Any]] = [
        {
            "key": None,
            "sel": draws[0]["sel"],
            "slot": SLOTS[draws[0]["sel"]],
            "mult": draws[0]["mult"],
            "status": draws[0]["status"],
            "slots": dict(draws[0]["slots"]),
        }
    ]
    for index, key in enumerate(keys):
        frame = draws[index + 1]
        trace.append(
            {
                "key": key,
                "sel": frame["sel"],
                "slot": SLOTS[frame["sel"]],
                "mult": frame["mult"],
                "status": frame["status"],
                "slots": dict(frame["slots"]),
            }
        )
    return {
        "meta": dict(meta),
        "initial_slots": dict(initial),
        "keys": list(keys),
        "prompt_responses": list(prompts),
        "trace": trace,
        "final": {
            "slots": dict(state.slots),
            "sel": state.sel,
            "slot": SLOTS[state.sel],
            "mult": state.mult,
            "status": state.status,
            "dirty": state.dirty(),
            "written": state.written,
            "quit": state.quit,
        },
        "writes": writes,
    }


def _build_session_data() -> Dict[str, Any]:
    return _capture_session(
        SESSION_INITIAL_SLOTS, SESSION_KEYS, SESSION_PROMPTS, SESSION_META
    )


def _write_fixture(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _load_fixture(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _maybe_regenerate() -> None:
    if os.environ.get("HUEBOX_REGENERATE") == "1":
        _write_fixture(COLOR_FIXTURE, _build_color_data())
        _write_fixture(SESSION_FIXTURE, _build_session_data())


def _assert_float_close(test: unittest.TestCase, got: float, want: float) -> None:
    test.assertTrue(
        abs(got - want) <= FLOAT_TOL, "got %r want %r within %g" % (got, want, FLOAT_TOL)
    )


class ParityColor(unittest.TestCase):
    def test_vectors_match(self) -> None:
        _maybe_regenerate()
        self.assertTrue(
            os.path.exists(COLOR_FIXTURE),
            "missing %s (regenerate with HUEBOX_REGENERATE=1)" % COLOR_FIXTURE,
        )
        data = _load_fixture(COLOR_FIXTURE)
        self.assertEqual(data["meta"]["hue_step"], color.HUE_STEP)
        self.assertEqual(data["meta"]["channel_step"], color.CHANNEL_STEP)
        self.assertEqual(data["meta"]["missing"], color.MISSING)
        self.assertEqual(data["meta"]["slots"], list(SLOTS))
        for case in data["hex_to_rgb"]:
            self.assertEqual(
                list(color.hex_to_rgb(case["input"])), case["expected"], case["input"]
            )
        for case in data["rgb_to_hex"]:
            self.assertEqual(
                color.rgb_to_hex(tuple(case["input"])), case["expected"], repr(case)
            )
        for case in data["rgb_to_hsv"]:
            got = list(color.rgb_to_hsv(tuple(case["input"])))
            self.assertEqual(len(got), len(case["expected"]), repr(case))
            for left, right in zip(got, case["expected"]):
                _assert_float_close(self, left, right)
        for case in data["hsv_to_rgb"]:
            args = case["input"]
            got = list(color.hsv_to_rgb(args[0], args[1], args[2]))
            self.assertEqual(len(got), len(case["expected"]), repr(case))
            for left, right in zip(got, case["expected"]):
                _assert_float_close(self, left, right)
        for case in data["luminance"]:
            _assert_float_close(self, color.luminance(case["input"]), case["expected"])
        for case in data["readable_fg"]:
            self.assertEqual(
                color.readable_fg(case["input"]), case["expected"], case["input"]
            )
        for case in data["is_hex"]:
            self.assertEqual(
                color.is_hex(case["input"]), case["expected"], repr(case["input"])
            )
        for case in data["normalize_hex"]:
            self.assertEqual(
                color.normalize_hex(case["input"]), case["expected"], case["input"]
            )
        for case in data["step_hsv"]:
            self.assertEqual(
                color.step_hsv(
                    case["value"], case["channel"], case["direction"], case["mult"]
                ),
                case["expected"],
                repr(case),
            )


class ParitySession(unittest.TestCase):
    def test_trace_replays(self) -> None:
        _maybe_regenerate()
        self.assertTrue(
            os.path.exists(SESSION_FIXTURE),
            "missing %s (regenerate with HUEBOX_REGENERATE=1)" % SESSION_FIXTURE,
        )
        data = _load_fixture(SESSION_FIXTURE)
        replayed = _capture_session(
            data["initial_slots"], data["keys"], data["prompt_responses"], data["meta"]
        )
        self.assertEqual(replayed["trace"], data["trace"])
        self.assertEqual(replayed["final"], data["final"])
        self.assertEqual(replayed["writes"], data["writes"])

    def test_script_covers_the_surface(self) -> None:
        _maybe_regenerate()
        self.assertTrue(os.path.exists(SESSION_FIXTURE))
        data = _load_fixture(SESSION_FIXTURE)
        keys = data["keys"]
        for key in ("w", "e", "s", "d", "x", "c", "H", "L"):
            self.assertIn(key, keys)
        self.assertGreaterEqual(keys.count("f"), 3)
        self.assertIn("u", keys)
        self.assertIn("r", keys)
        self.assertTrue("h" in keys or "X" in keys)
        self.assertIn(SAVE, keys)
        mults = [frame["mult"] for frame in data["trace"]]
        for step in (1, 5, 20):
            self.assertIn(step, mults)
        self.assertTrue(data["writes"])
        self.assertFalse(data["final"]["dirty"])


if __name__ == "__main__":
    unittest.main()
