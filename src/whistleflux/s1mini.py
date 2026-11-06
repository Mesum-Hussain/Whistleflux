"""Smart clean-up on this computer with S1-mini by Superwhisper, run by llama.cpp.

S1-mini (https://huggingface.co/superwhisper/s1-mini) is a 0.6B model trained for one
job: turning a raw speech-to-text transcript into clean written text. English only.
License: Apache 2.0, plus the term that it keeps its name, "S1-mini" by "Superwhisper".

`whistleflux download s1-mini` fetches two things once and checks them against the
SHA-256 hashes pinned below:
- s1-mini-q4_k_m.gguf: the model, 484 MB.
- llama.cpp's CPU build for this OS (llama-server and its libraries, about 50 MB).

llama-server runs as a child process listening on 127.0.0.1 only, with a random API key,
so nothing leaves the computer and other users can't use it.
"""

import atexit
import json
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from whistleflux import paths
from whistleflux.speech import _fetch_verified

log = logging.getLogger(__name__)

# Pinned commit on Hugging Face, so an upstream change can't swap the file under us.
MODEL_URL = (
    "https://huggingface.co/superwhisper/s1-mini-GGUF/resolve/"
    "34add00a48a2e5d24e5a4ee5405a99620a3a240c/s1-mini-q4_k_m.gguf"
)
MODEL_SHA256 = "3b41ebe2502cbd03e811d5d16b022f5ab551eda58d62597d152f89535003c634"
MODEL_FILE = "s1-mini-q4_k_m.gguf"

# The build behind llama.cpp's v0.9.0 release.
ENGINE_BUILD = "b12110"
ENGINE_URL = "https://github.com/ggml-org/llama.cpp/releases/download/{build}/llama-{build}-bin-{tag}"
ENGINE_SHA256 = {  # by asset name suffix
    "ubuntu-x64.tar.gz": "368227edcc229be5f9bed16a2223d79b5e41d0ac2e19bcf5825e82291c060e55",
    "ubuntu-arm64.tar.gz": "4758b51335e37b4346251f2687d64841ff1c07ecb27fc46323945bb2ec9c44d2",
    "macos-arm64.tar.gz": "4f5b8de12e0b9516658ca6a5764c6ffb3d7c659c3dd538556b1413c7b4050662",
    "macos-x64.tar.gz": "c8094268b6bf62f4998f0e45656fd9f5e2d4afb6b1cd0044d761db64be1dd47d",
    "win-cpu-x64.zip": "98901e327c252875accaf2d1bf364e10e868843b0b63468baad1aa945cdc5da2",
    "win-cpu-arm64.zip": "f8d20fb07d0b499988ced8963afcd5ded2d2cdd9983b757fbc7e51c4abd722e8",
}
SERVER_FILE = "llama-server.exe" if sys.platform == "win32" else "llama-server"

# Required by the model, word for word (see its model card).
SYSTEM_PROMPT = (
    "You are a text normalizer for speech-to-text transcripts. The input begins "
    "with a control line specifying the styling, structure, and context settings; "
    "clean the transcript to match those settings and output only the cleaned text."
)
# The only values the model was trained on; anything else can make it garble the text.
CHOICES = {
    "styling": ("casual", "semi-casual", "semi-formal", "formal"),
    "structure": ("prose", "lists"),
    "context": ("general", "email"),
}
DEFAULTS = {"styling": "semi-formal", "structure": "lists", "context": "general"}

CONTEXT_TOKENS = 3072   # prompt + transcript + answer; also caps the memory it uses
# About 200 tokens. The model card allows ~1,000, but in tests smaller pieces were just as
# fast in total and better at spotting lists.
MAX_CHUNK_CHARS = 800
# Extra seconds per character on top of [format] timeout: it writes ~150 characters a
# second on a laptop CPU, so this leaves about 3x headroom.
SECONDS_PER_CHAR = 0.02


def model_dir() -> Path:
    return paths.data_dir() / "models" / "s1-mini"


def engine_dir() -> Path:
    return model_dir() / "engine"


def is_downloaded() -> bool:
    return (model_dir() / MODEL_FILE).is_file() and (engine_dir() / SERVER_FILE).is_file()


def _engine_tag() -> str:
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if arch is None:
        raise RuntimeError(f"llama.cpp isn't available for {platform.machine()} processors")
    if sys.platform == "darwin":
        return f"macos-{arch}.tar.gz"
    if sys.platform == "win32":
        return f"win-cpu-{arch}.zip"
    if platform.libc_ver()[0] != "glibc":
        raise RuntimeError("llama.cpp's Linux builds need glibc (musl isn't supported)")
    return f"ubuntu-{arch}.tar.gz"


def _wanted(name: str) -> bool:
    """llama-server, the shared libraries it loads and the license files; skip the other tools."""
    base = name.rsplit("/", 1)[-1]
    return (base == SERVER_FILE or base.endswith((".dll", ".dylib")) or ".so" in base
            or base.startswith("LICENSE"))


def _strip_top(name: str) -> str:
    """Linux and macOS archives put everything in llama-bNNNN/; Windows ones don't."""
    first, _, rest = name.partition("/")
    return rest if first.startswith("llama-") and rest else name


def extract_engine(archive: Path, dest: Path) -> None:
    """Unpack llama-server and its libraries from a llama.cpp release into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = _strip_top(info.filename)
                if info.is_dir() or "/" in name or not _wanted(name):
                    continue
                (dest / name).write_bytes(zf.read(info))
        return
    with tarfile.open(archive) as tf:
        members = []
        for m in tf.getmembers():
            name = _strip_top(m.name)
            if not (m.isfile() or m.issym()) or "/" in name or not _wanted(name):
                continue
            m.name = name
            members.append(m)
        # The "data" filter refuses absolute paths and links that point outside dest.
        if hasattr(tarfile, "data_filter"):
            tf.extractall(dest, members, filter="data")
        else:
            tf.extractall(dest, members)


def download() -> Path:
    """Download and verify the model and llama.cpp. Prints progress to stderr."""
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)

    part = d / f"{MODEL_FILE}.part"
    _fetch_verified(MODEL_URL, MODEL_SHA256, part, "S1-mini model")
    part.replace(d / MODEL_FILE)

    tag = _engine_tag()
    archive = d / f"llama-{tag}.part"
    _fetch_verified(ENGINE_URL.format(build=ENGINE_BUILD, tag=tag), ENGINE_SHA256[tag], archive, "llama.cpp")
    archive = archive.rename(d / f"llama-{tag}")  # extract_engine looks at the extension
    tmp = d / "engine.part"
    shutil.rmtree(tmp, ignore_errors=True)
    extract_engine(archive, tmp)
    if not (tmp / SERVER_FILE).is_file():
        raise RuntimeError(f"{SERVER_FILE} is missing from the llama.cpp download")
    shutil.rmtree(engine_dir(), ignore_errors=True)
    tmp.rename(engine_dir())
    archive.unlink()
    return d


def server_log() -> Path:
    return paths.data_dir() / "s1-mini-server.log"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """A llama-server child process with S1-mini loaded. Started once, reused per dictation."""

    def __init__(self):
        if not is_downloaded():
            raise FileNotFoundError("S1-mini isn't downloaded. Run: whistleflux download s1-mini")
        self.port = _free_port()
        self.key = secrets.token_hex(16)
        cmd = [
            str(engine_dir() / SERVER_FILE),
            "--model", str(model_dir() / MODEL_FILE),
            "--host", "127.0.0.1", "--port", str(self.port),
            "--api-key", self.key,
            "--ctx-size", str(CONTEXT_TOKENS),
            "--parallel", "1",
            "--cache-ram", "0",  # don't keep old prompts in memory (default: up to 8 GB)
            "--no-webui",
            "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}',
            "--temp", "0",
        ]
        env = dict(os.environ)
        if paths.IS_LINUX:
            env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [str(engine_dir()), env.get("LD_LIBRARY_PATH")]))
        kwargs: dict = {}
        if paths.IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        server_log().parent.mkdir(parents=True, exist_ok=True)
        with server_log().open("wb") as out:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                cwd=engine_dir(), env=env, **kwargs,
            )
        atexit.register(self.close)
        self._wait_ready()

    def _wait_ready(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with code {self.proc.returncode}:\n{self._log_tail()}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (OSError, ValueError):
                pass  # not listening yet, or 503 while the model loads
            time.sleep(0.2)
        self.close()
        raise RuntimeError(f"llama-server didn't start within {timeout:.0f}s:\n{self._log_tail()}")

    def _log_tail(self) -> str:
        try:
            return "\n".join(server_log().read_text(encoding="utf-8", errors="replace").splitlines()[-10:])
        except OSError:
            return "(no log)"

    def alive(self) -> bool:
        return self.proc.poll() is None

    def chat(self, body: dict, timeout: float) -> str:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"].get("content") or ""

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


_server: Server | None = None
_lock = threading.Lock()


def get_server() -> Server:
    """The running server, started (or restarted after a crash) on first use."""
    global _server
    with _lock:
        if _server is None or not _server.alive():
            start = time.monotonic()
            _server = Server()
            log.info("S1-mini loaded in %.1fs", time.monotonic() - start)
        return _server


def stop() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.close()
            _server = None


def settings(cfg: dict) -> dict:
    """styling/structure/context from the [s1_mini] config section, checked."""
    section = cfg.get("s1_mini", {})
    out = {}
    for axis, default in DEFAULTS.items():
        value = str(section.get(axis, default)).strip().lower()
        if value not in CHOICES[axis]:
            log.warning("s1_mini.%s = %r isn't one of %s; using %r", axis, value, CHOICES[axis], default)
            value = default
        out[axis] = value
    return out


def request_body(transcript: str, s: dict) -> dict:
    control = f"[Styling: {s['styling']}] [Structure: {s['structure']}] [Context: {s['context']}]"
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{control}\n{transcript}"},
        ],
        "temperature": 0,
        # Output length tracks input length; the model card suggests 1.3 x input + 32.
        "max_tokens": int(len(transcript) / 3 * 1.3) + 32,
        "chat_template_kwargs": {"enable_thinking": False},
        # Reusing the previous request's cached prompt shifts the numbers slightly, and the
        # same dictation then comes out differently from one time to the next.
        "cache_prompt": False,
    }


def _chunks(line: str) -> list[str]:
    """Split a long line at sentence ends (or spaces) into pieces the model handles well."""
    pieces = []
    while len(line) > MAX_CHUNK_CHARS:
        window = line[:MAX_CHUNK_CHARS]
        cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
        if cut < MAX_CHUNK_CHARS // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = MAX_CHUNK_CHARS - 1
        pieces.append(line[:cut + 1].strip())
        line = line[cut + 1:].strip()
    if line:
        pieces.append(line)
    return pieces


def _clean_piece(server: Server, piece: str, s: dict, timeout: float) -> str:
    out = server.chat(request_body(piece, s), timeout + len(piece) * SECONDS_PER_CHAR).strip()
    # An empty answer is right for filler-only input ("um"), but not for a real sentence.
    if not out and len(piece.split()) > 3:
        log.warning("S1-mini returned nothing for %r; keeping it as is", piece[:200])
        return piece
    # A much longer output means the model went off the rails.
    if len(out) > 2 * len(piece) + 40:
        log.warning("S1-mini output looks wrong, keeping the input: %r", out[:200])
        return piece
    return out


_LIST_LINE = re.compile(r"\s*(?:- |\d+\. )")


def _join(pieces: list[str]) -> str:
    """Join the cleaned pieces of one long line with a space, or with a line break where
    one ends or the next starts with a list item, so items aren't glued onto a sentence."""
    out = pieces[0] if pieces else ""
    for prev, p in zip(pieces, pieces[1:]):
        at_list = _LIST_LINE.match(prev.rsplit("\n", 1)[-1]) or _LIST_LINE.match(p.split("\n", 1)[0])
        out += ("\n" if at_list else " ") + p
    return out


# "first", "secondly", "3rd", "number four", "number 4": the item's position in a counted list.
_WORDS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
_NUMBERS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_MARKER = re.compile(
    r"\b(?:(?P<word>" + "|".join(_WORDS) + r")(?:ly)?|(?P<nth>\d{1,2})(?:st|nd|rd|th)"
    r"|number\s+(?P<num>" + "|".join(_NUMBERS) + r"|\d{1,2})|(?P<last>finally|lastly))\b[,:]?[ \t]*",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _position(m: re.Match) -> int:
    """1 for "first", 2 for "second"...; 0 for "finally"/"lastly" (only valid as the last item)."""
    if m["word"]:
        return _WORDS.index(m["word"].lower()) + 1
    if m["nth"]:
        return int(m["nth"]) or -1  # "0th" is never a list position
    if m["num"]:
        num = m["num"].lower()
        return _NUMBERS.index(num) + 1 if num in _NUMBERS else int(num)
    return 0


def _starts_clause(text: str, at: int, first: bool, colon: bool = False) -> bool:
    """Only count a marker that begins a sentence, line, list item or clause: "At first I..."
    and "the second time" aren't list items. Later markers may also follow "and", and the
    first one may be followed by a colon instead ("three things to do first: buy milk")."""
    if first and colon:
        return True
    before = text[:at].rstrip(" \t")
    if before.endswith("-"):
        head = before[:-1].rstrip(" \t")
        if not head or head.endswith("\n"):
            return True  # "- First, ..." bullet line
    if not before or before[-1] in "\n.!?:;,":
        return True
    return not first and before.lower().endswith(" and")


def _item(text: str) -> str:
    text = text.strip().removeprefix("- ").strip()
    text = re.sub(r"(?:[,;]|\s+and|\s*\n-)+$", "", text.rstrip()).strip()
    if text.endswith(".") and not _SENTENCE_END.search(text[:-1]):
        text = text[:-1]  # one sentence per item: drop the period, like "- " list items
    return text[:1].upper() + text[1:]


def number_lists(text: str) -> str:
    """Turn counted items into a numbered list (S1-mini only does this some of the time):
    "We need three things: first, milk; second, eggs; and finally, bread."
    becomes "We need three things:\n1. Milk\n2. Eggs\n3. Bread".
    Needs "first" then "second" (or "number one", "1st"...) in order, each starting a
    clause or line; "finally" or "lastly" can end the list. Anything else is left alone.
    """
    marks = list(_MARKER.finditer(text))
    i = 0
    while i < len(marks):
        colon = marks[i].group().rstrip().endswith(":")
        if _position(marks[i]) != 1 or not _starts_clause(text, marks[i].start(), first=True, colon=colon):
            i += 1
            continue
        run = [marks[i]]
        for m in marks[i + 1:]:
            pos = _position(m)
            if not _starts_clause(text, m.start(), first=False):
                continue  # e.g. "the second one" inside an item
            if pos == len(run) + 1:
                run.append(m)
            elif pos == 0 and len(run) >= 2:
                run.append(m)
                break
            else:
                break
        if len(run) < 2:
            i += 1
            continue
        items = [text[a.end():b.start()] for a, b in zip(run, run[1:])]
        # The last item runs to the end of its sentence (or line); the rest is kept after the list.
        tail = text[run[-1].end():]
        end = len(tail)
        line_end = tail.find("\n")
        if line_end != -1:
            end = line_end
        sentence = _SENTENCE_END.search(tail[:end])
        if sentence:
            end = sentence.end()
        items.append(tail[:end])
        rest = tail[end:].strip()
        items = [_item(t) for t in items]
        if not all(items):
            i += 1
            continue
        intro = text[:run[0].start()].rstrip()
        if intro.endswith("\n-") or intro == "-":
            intro = intro[:-1].rstrip()
        if intro.endswith((",", ";")):
            intro = intro[:-1] + ":"
        elif intro and intro[-1].isalnum():  # "...to do first: buy milk"
            intro += ":"
        numbered = "\n".join(f"{n}. {t}" for n, t in enumerate(items, 1))
        text = "\n".join(part for part in (intro, numbered) if part)
        start_of_rest = len(text)
        if rest:
            text += "\n" + rest
        # Look for another counted list after this one.
        marks = [m for m in _MARKER.finditer(text) if m.start() > start_of_rest]
        i = 0
    return text


def _counted_lines(lines: list[str]) -> dict[int, str]:
    """Lines that count off a list one per line ("first buy milk" / "second call mom"),
    as {line index: the item without its marker}. Needs 2+ lines in a row starting with
    "first", "second"... in order; "finally" or "lastly" can end the run."""
    found: dict[int, str] = {}
    run: list[tuple[int, str]] = []

    def close():
        if len(run) >= 2:
            found.update(run)
        run.clear()

    for i, line in enumerate(lines):
        m = _MARKER.match(line.strip())
        item = line.strip()[m.end():].strip() if m else ""
        pos = _position(m) if m and item else -1
        if run and pos == 0:
            run.append((i, item))
            close()
        elif run and pos == len(run) + 1:
            run.append((i, item))
        else:
            close()
            if pos == 1:
                run.append((i, item))
    close()
    return found


def format_text(text: str, cfg: dict) -> str:
    """Clean up text with S1-mini. On any problem, return text unchanged.

    Line breaks (from "new line" / "new paragraph", already applied by the basic
    clean-up) are kept: each line is cleaned on its own.
    """
    language = cfg.get("speech", {}).get("language", "")
    if language and language != "en":
        log.info("S1-mini is English-only; skipping it for language %r", language)
        return text
    if not is_downloaded():
        log.warning("S1-mini isn't downloaded (run: whistleflux download s1-mini); using basic clean-up")
        return text
    s = settings(cfg)
    timeout = float(cfg["format"].get("timeout", 10))
    try:
        server = get_server()
        raw = text.split("\n")
        # Clean a counted item without its "second": alone, "second call mom" reads to the
        # model as "Second call, Mom."
        counted = _counted_lines(raw)
        lines = []
        number = 0
        for i, line in enumerate(raw):
            if not line.strip():
                lines.append("")  # blank line from "new paragraph"
                number = 0
                continue
            pieces = [_clean_piece(server, p, s, timeout) for p in _chunks(counted.get(i, line.strip()))]
            cleaned = _join([p for p in pieces if p])
            if not cleaned:  # a line that was only filler disappears entirely
                continue
            if i in counted:
                starts_run = _position(_MARKER.match(line.strip())) == 1
                number = 1 if starts_run else number + 1
                cleaned = f"{number}. {_item(cleaned)}"
            lines.append(cleaned)
    except (OSError, RuntimeError, ValueError, KeyError, IndexError) as e:
        log.error("S1-mini clean-up failed, using basic clean-up: %r", e)
        return text
    out = "\n".join(lines).strip("\n")
    # S1-mini only makes "- " lists; number the ones the speaker counted out.
    return number_lists(out) if s["structure"] == "lists" else out
