"""Config file: defaults below, overridden by the user's config.toml."""

import tomllib
from pathlib import Path

from whistleflux import paths

DEFAULT_CONFIG = """\
# Whistleflux configuration

[speech]
# Speech-to-text runs on this computer with Cactus Whistle (17 MB, `whistleflux download`).
language = ""                # "en", "de", "fr", "es", "it", "nl" or "pl"; "" = detect it
# Names and jargon the speech model should favor, e.g. ["Whistleflux", "Kubernetes"]:
keywords = []
silence_threshold_db = -50   # recordings quieter than this are skipped

[format]
# Remove "um"/"uh", stutters like "the the", apply "scratch that" and "new line".
# Simple rules, applied before the smart clean-up.
basic_cleanup = true
# Then a smart clean-up with S1-mini by Superwhisper: self-corrections, punctuation, lists,
# numbers, emails. Runs on this computer, so nothing leaves it. English only: with another
# `language` above, only the basic clean-up is applied.
# Download it once (about 510 MB) with `whistleflux download s1-mini`.
enabled = true
timeout = 10                 # seconds, plus 1 per 50 characters dictated; on timeout or
                             # error the basic clean-up is typed

[s1_mini]
# Only these values work; the model was trained on exactly these.
styling = "semi-formal"      # "casual", "semi-casual", "semi-formal" or "formal"
structure = "lists"          # "lists": spoken lists become "- " or "1. " lines; "prose": fewer lists
context = "general"          # "email" lays out a greeting, body and sign-off

[output]
sounds = true                # short beeps when recording starts and stops
overlay = true               # sound-wave popup at the bottom of the screen while recording
key_delay_ms = 5             # Linux: pause between key presses
# Linux: tap this key before and after typing, to switch to a US QWERTY layout and back.
# One of: leftalt, rightalt, leftctrl, rightctrl, leftshift, rightshift, super
wrap_key = ""

[hotkey]
# macOS and Windows: global shortcut that starts and stops recording (pynput syntax).
# On Linux, bind `whistleflux` to a shortcut in your desktop settings instead.
toggle = "<ctrl>+<alt>+space"
"""


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    cfg = tomllib.loads(DEFAULT_CONFIG)
    path = paths.config_file()
    if path.exists():
        with path.open("rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    return cfg


def write_default() -> Path:
    path = paths.config_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return path

