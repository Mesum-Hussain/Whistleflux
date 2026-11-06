"""Background process: keeps the model loaded and handles start/stop requests.

Requests arrive over a local socket (named pipe on Windows) from `whistleflux toggle`,
or from the built-in global hotkey on macOS and Windows.
"""

import logging
import os
import threading
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Listener

from whistleflux import audio, paths, s1mini
from whistleflux.formatter import format_text
from whistleflux.output import make_typer
from whistleflux.overlay import OverlayClient
from whistleflux.speech import from_config

log = logging.getLogger(__name__)

TONE_START, TONE_STOP, TONE_ERROR = 880, 660, 330
MIN_SECONDS = 0.3


class Daemon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state = "idle"  # idle -> recording -> processing -> idle
        self._lock = threading.Lock()
        start = time.monotonic()
        self.transcriber = from_config(cfg)
        log.info("Speech model loaded in %.1fs", time.monotonic() - start)
        fmt = cfg["format"]
        if fmt.get("enabled", True) and s1mini.is_downloaded():
            # Load it now so the first dictation isn't slower; a failure is retried then.
            try:
                s1mini.get_server()
            except Exception:
                log.exception("Couldn't start S1-mini")
        self.typer = make_typer(cfg)
        self.recorder = audio.Recorder()
        self.overlay = OverlayClient() if cfg["output"].get("overlay", True) else None
        self._overlay("hide")  # start the overlay process now so it appears instantly later

    def _overlay(self, cmd: str) -> None:
        if self.overlay is not None:
            self.overlay.send(cmd)

    def _feed_levels(self) -> None:
        while self.state == "recording":
            self._overlay(f"level {self.recorder.level:.3f}")
            time.sleep(1 / 30)

    def _beep(self, freq: int) -> None:
        if self.cfg["output"].get("sounds", True):
            audio.beep(freq)

    def toggle(self) -> str:
        with self._lock:
            if self.state == "idle":
                self._overlay("show")
                self._beep(TONE_START)
                try:
                    self.recorder.start()
                except Exception:
                    self._overlay("hide")
                    raise
                self.state = "recording"
                threading.Thread(target=self._feed_levels, daemon=True).start()
            elif self.state == "recording":
                try:
                    pcm = self.recorder.stop()
                except Exception:
                    self.state = "idle"
                    raise
                self.state = "processing"
                self._overlay("processing")
                threading.Thread(
                    target=self._process, args=(pcm, self.recorder.sample_rate), daemon=True
                ).start()
            return self.state

    def cancel(self) -> str:
        with self._lock:
            if self.state == "recording":
                try:
                    self.recorder.stop()
                finally:
                    self.state = "idle"
                    self._overlay("hide")
                self._beep(TONE_ERROR)
            return self.state

    def _process(self, pcm, rate: int) -> None:
        try:
            self._beep(TONE_STOP)
            seconds = len(pcm) / rate
            level = audio.peak_db(pcm)
            if seconds < MIN_SECONDS or level < self.cfg["speech"]["silence_threshold_db"]:
                log.info("Skipped %.1fs recording (peak %.0f dB): silent or too short", seconds, level)
                self._beep(TONE_ERROR)
                return
            t0 = time.monotonic()
            raw = self.transcriber.transcribe(pcm, rate)
            t1 = time.monotonic()
            text = format_text(raw, self.cfg)
            t2 = time.monotonic()
            log.info(
                "%.1fs audio | transcribe %.2fs | format %.2fs\n  raw: %s\n  out: %s",
                seconds, t1 - t0, t2 - t1, raw, text,
            )
            self._overlay("hide")
            if text:
                self.typer.type(text)
        except Exception:
            log.exception("Processing failed")
            self._beep(TONE_ERROR)
        finally:
            self._overlay("hide")
            with self._lock:
                self.state = "idle"

    def _handle(self, cmd: str) -> str:
        try:
            if cmd == "toggle":
                return self.toggle()
            if cmd == "cancel":
                return self.cancel()
            if cmd == "status":
                return self.state
            return f"error: unknown command {cmd!r}"
        except Exception as e:
            log.exception("%s failed", cmd)
            return f"error: {e}"

    def _start_hotkey(self) -> None:
        combo = self.cfg["hotkey"].get("toggle")
        if paths.IS_LINUX or not combo:
            return
        from pynput.keyboard import GlobalHotKeys

        GlobalHotKeys({combo: lambda: self._handle("toggle")}).start()
        log.info("Hotkey %s active", combo)

    def serve(self) -> None:
        address, family = paths.ipc_address()
        if family == "AF_UNIX" and not address.startswith("\0"):
            # Leftover socket file from a crash; the caller checked nothing answers on it.
            try:
                os.unlink(address)
            except FileNotFoundError:
                pass
        with Listener(address, family, authkey=paths.ipc_key()) as listener:
            self._start_hotkey()
            log.info("Ready")
            while True:
                try:
                    conn = listener.accept()
                except (AuthenticationError, OSError) as e:
                    log.warning("Rejected connection: %s", e)
                    continue
                with conn:
                    try:
                        cmd = conn.recv_bytes().decode()
                    except (EOFError, OSError):
                        continue
                    if cmd == "quit":
                        conn.send_bytes(b"stopped")
                        break
                    conn.send_bytes(self._handle(cmd).encode())
        if self.overlay is not None:
            self.overlay.close()
        if hasattr(self.typer, "close"):
            self.typer.close()
        s1mini.stop()
        log.info("Stopped")
