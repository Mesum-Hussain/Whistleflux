"""Command line: `whistleflux` starts/stops dictation; the rest are helpers."""

import argparse
import logging
import logging.handlers
import subprocess
import sys
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
from pathlib import Path

from whistleflux import __version__, audio, config, paths, speech

log = logging.getLogger("whistleflux")


def _send(cmd: str) -> str | None:
    """Send a command to the daemon. None if it isn't running."""
    address, family = paths.ipc_address()
    try:
        with Client(address, family, authkey=paths.ipc_key()) as conn:
            conn.send_bytes(cmd.encode())
            return conn.recv_bytes().decode()
    except (OSError, EOFError, AuthenticationError):
        return None


def _spawn_daemon() -> subprocess.Popen:
    exe = sys.executable
    kwargs: dict = dict(
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if paths.IS_WINDOWS:
        pythonw = Path(exe).with_name("pythonw.exe")
        if pythonw.exists():
            exe = str(pythonw)  # no console window
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([exe, "-m", "whistleflux", "daemon"], **kwargs)


def _last_error() -> str:
    """The final exception message from the log, e.g. why the daemon died."""
    try:
        lines = paths.log_file().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return "  (no log)"
    # The exception message follows the last traceback's final "File ..." frame.
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(("Traceback", "  File ", "    ")):
            msg = [line for line in lines[i + 1:] if line.strip()]
            return "\n".join("  " + line for line in msg[-8:])
    return "\n".join("  " + line for line in lines[-5:])


def _setup_logging(to_file: bool) -> None:
    handlers: list[logging.Handler] = []
    if sys.stderr:  # None under pythonw
        handlers.append(logging.StreamHandler())
    if to_file:
        paths.log_file().parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(paths.log_file(), maxBytes=1_000_000, backupCount=1, encoding="utf-8")
        )
    logging.basicConfig(
        level=logging.INFO if to_file else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def cmd_toggle(args) -> int:
    reply = _send("toggle")
    if reply is None:
        if not speech.is_downloaded():
            print("The speech model isn't downloaded yet. Run: whistleflux download", file=sys.stderr)
            return 1
        proc = _spawn_daemon()
        for _ in range(150):  # model load takes a few seconds
            time.sleep(0.2)
            if _send("status") is not None:
                break
            if proc.poll() is not None:  # it crashed; no point waiting
                break
        if _send("status") is None:
            print("Whistleflux didn't start:", file=sys.stderr)
            print(_last_error(), file=sys.stderr)
            print(f"Full log: {paths.log_file()}", file=sys.stderr)
            return 1
        reply = _send("toggle")
    print(reply)
    return 1 if reply is None or reply.startswith("error") else 0


def cmd_simple(args) -> int:
    reply = _send(args.command)
    print(reply or "not running")
    return 0


def cmd_stop(args) -> int:
    print(_send("quit") or "not running")
    return 0


def cmd_daemon(args) -> int:
    if _send("status") is not None:
        print("Already running")
        return 0
    _setup_logging(to_file=True)
    from whistleflux.daemon import Daemon

    try:
        Daemon(config.load()).serve()
    except Exception:
        log.exception("Daemon failed")
        return 1
    return 0


def cmd_download(args) -> int:
    module = speech
    if args.model == "s1-mini":
        from whistleflux import s1mini as module
    if module.is_downloaded() and not args.force:
        print(f"Already downloaded: {module.model_dir()}")
        return 0
    print(f"Saved to: {module.download()}")
    if args.model == "s1-mini" and _send("status") is not None:
        print("Restart to start using it: whistleflux stop")
    return 0


def cmd_transcribe(args) -> int:
    _setup_logging(to_file=False)
    from whistleflux.formatter import format_text

    cfg = config.load()
    pcm, rate = audio.read_wav(Path(args.file))
    raw = speech.from_config(cfg).transcribe(pcm, rate)
    if args.raw:
        print(raw)
        return 0
    print(f"raw: {raw}", file=sys.stderr)
    print(format_text(raw, cfg))
    return 0


def cmd_config(args) -> int:
    print(config.write_default())
    return 0


def cmd_log(args) -> int:
    try:
        lines = paths.log_file().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        print("No log yet")
        return 0
    print("\n".join(lines[-args.lines:]))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="whistleflux",
        description="Dictate anywhere. Run once to start recording, again to stop and type the text.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("toggle", help="start or stop recording (the default)").set_defaults(func=cmd_toggle)
    sub.add_parser("cancel", help="stop recording without typing anything").set_defaults(func=cmd_simple)
    sub.add_parser("status", help="show whether it's idle, recording or processing").set_defaults(func=cmd_simple)
    sub.add_parser("stop", help="stop the background process").set_defaults(func=cmd_stop)
    sub.add_parser("daemon", help="run the background process in the foreground").set_defaults(func=cmd_daemon)
    p = sub.add_parser("download", help="download the speech model (19 MB), or S1-mini (about 510 MB)")
    p.add_argument("model", nargs="?", choices=("speech", "s1-mini"), default="speech",
                   help="speech (default), or s1-mini for smart clean-up on this computer")
    p.add_argument("--force", action="store_true", help="download again even if present")
    p.set_defaults(func=cmd_download)
    p = sub.add_parser("transcribe", help="transcribe a 16-bit WAV file and print the text")
    p.add_argument("file")
    p.add_argument("--raw", action="store_true", help="print the transcript without any clean-up")
    p.set_defaults(func=cmd_transcribe)
    sub.add_parser("config", help="create the config file if needed and print its path").set_defaults(func=cmd_config)
    p = sub.add_parser("log", help="show the end of the log")
    p.add_argument("-n", "--lines", type=int, default=40)
    p.set_defaults(func=cmd_log)

    args = parser.parse_args()
    sys.exit(args.func(args) if args.command else cmd_toggle(args))
