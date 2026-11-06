"""Basic clean-up of a transcript with simple rules: no AI, no network, instant.

Removes filler sounds and stutters, applies "scratch that", and turns "new line" and
"new paragraph" into line breaks. It only deletes or moves text the speaker clearly
didn't mean to keep, so it never changes the meaning. Smarter fixes (self-corrections,
lists, numbers) are left to S1-mini (s1mini.py).

The Android app has the same rules (mobile/.../engine/Cleanup.kt). Both are checked
against shared/cleanup-cases.json, so change them together.
"""

import re

# um, ummm, uh, uhm, er, erm, ah, hmm, mm, mhm (any number of repeated letters)
FILLER = re.compile(r"(?:u+m+|u+h+m*|e+r+m*|a+h+|h+m+|m{2,}|m+h+m+)")

# Words people repeat on purpose ("no, no", "had had", "that that").
KEEP_REPEATED = {"no", "yes", "very", "so", "that", "had", "ha", "bye", "really", "well", "go"}

# "a new line of products" is not a command.
NOT_BEFORE_COMMAND = {"a", "an", "the", "this", "that", "one", "each", "every", "another", "of", "my", "our", "your"}

SENTENCE_END = ".!?"
EDGE_PUNCT = ".,!?;:\"'()[]"

_SCRATCH = re.compile(r"\bscratch that\b[.,!?]*", re.IGNORECASE)
_COMMAND = re.compile(r"\bnew (line|paragraph)\b", re.IGNORECASE)


def _core(token: str) -> str:
    return token.strip(EDGE_PUNCT).lower()


def _capitalize(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
    return text


def _apply_scratch_that(text: str) -> str:
    """Delete the sentence (or start of the text) before each "scratch that"."""
    while m := _SCRATCH.search(text):
        before = text[: m.start()].rstrip().rstrip(".,!?;: ")
        cut = max(before.rfind(c) for c in SENTENCE_END + "\n")
        kept = before[: cut + 1] if cut >= 0 else ""
        after = text[m.end():].lstrip()
        if not kept or kept[-1] in SENTENCE_END + "\n":
            after = _capitalize(after)
        text = (kept + " " + after).strip()
    return text


def _apply_commands(text: str) -> str:
    out, pos = [], 0
    for m in _COMMAND.finditer(text):
        words = text[: m.start()].split()
        if words and _core(words[-1]) in NOT_BEFORE_COMMAND:
            continue
        out.append(text[pos: m.start()].rstrip(" ,"))
        out.append("\n" if m.group(1).lower() == "line" else "\n\n")
        pos = m.end()
        while pos < len(text) and text[pos] in " ,.":
            pos += 1
    out.append(text[pos:])
    return "".join(out)


def _clean_line(line: str) -> str:
    tokens = line.split()
    kept: list[str] = []
    capitalize_next = False  # set when we drop words at the start of a sentence
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        at_start = not kept or kept[-1][-1] in SENTENCE_END
        core = _core(tok)

        if FILLER.fullmatch(core):
            # Keep sentence-ending punctuation that was attached to the filler: "coffee uh."
            if tok[-1] in SENTENCE_END and kept:
                kept[-1] = kept[-1].rstrip(",;:")
                if not kept[-1]:  # the previous token was just a comma
                    kept.pop()
                if kept and kept[-1][-1] not in SENTENCE_END:
                    kept[-1] += tok[-1]
            if at_start:
                capitalize_next = True
            i += 1
            continue

        # Stutters: "the the", "I, I think", "I think I think". Drop the first copy.
        skip = 0
        for n in (3, 2, 1):
            a, b = tokens[i: i + n], tokens[i + n: i + 2 * n]
            if len(b) < n or not core or [_core(t) for t in a] != [_core(t) for t in b]:
                continue
            if n == 1 and core in KEEP_REPEATED:
                continue
            if a[-1][-1] in SENTENCE_END:  # "Yes. Yes." is two sentences
                continue
            skip = n
            break
        if skip:
            if at_start:
                capitalize_next = True
            i += skip
            continue

        if capitalize_next:
            tok = _capitalize(tok)
            capitalize_next = False
        kept.append(tok)
        i += 1

    return re.sub(r"^[,;:\s]+", "", " ".join(kept))


def clean(text: str) -> str:
    text = _apply_scratch_that(text)
    text = _apply_commands(text)
    lines = [_clean_line(line) for line in text.split("\n")]
    # Start each new line with a capital, like a new sentence.
    lines = [_capitalize(line) if n > 0 else line for n, line in enumerate(lines)]
    return "\n".join(lines).strip()
