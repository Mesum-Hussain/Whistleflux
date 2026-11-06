"""Clean up a raw transcript before it's typed: basic rules, then S1-mini (s1mini.py).

Both run on this computer. If S1-mini is off, not downloaded or fails, the rule-cleaned
transcript is typed, so dictation never gets lost.
"""

from whistleflux import cleanup, s1mini


def format_text(text: str, cfg: dict) -> str:
    fmt = cfg["format"]
    if fmt.get("basic_cleanup", True):
        text = cleanup.clean(text)
    if not fmt.get("enabled", True) or not text.strip():
        return text
    return s1mini.format_text(text, cfg)
