"""The ``keystats run`` loop: find the keyboard, read events passively, reconnect."""

from __future__ import annotations

import errno
import glob
import os
import select
import signal
import sys
import time

from . import control
from .config import Config
from .detector import Detector
from .stats import Stats, load, save

RETRY_SECONDS = 2.0


def log(msg: str) -> None:
    # Goes to the journal under systemd. Never pass key data in here.
    print(f"keystats: {msg}", file=sys.stderr, flush=True)


def _evdev():
    try:
        import evdev
    except ImportError:
        sys.exit("keystats: python-evdev is not installed (pip install evdev)")
    return evdev


def list_devices() -> list[tuple[str, str | None]]:
    """(path, name) for every event device; name is None if unreadable."""
    evdev = _evdev()
    out = []
    for path in sorted(glob.glob("/dev/input/event*"), key=lambda p: int(p.rsplit("event", 1)[1])):
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            out.append((path, None))
            continue
        out.append((path, dev.name))
        dev.close()
    return out


def find_keyboards(name: str):
    """Open every event device whose name matches exactly and that has letter keys.

    Matching by exact name skips the "<name> Mouse", "System Control" and
    "Consumer Control" interfaces. The device is never grabbed.
    """
    evdev = _evdev()
    found, unreadable = [], 0
    for path in glob.glob("/dev/input/event*"):
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            unreadable += 1
            continue
        caps = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
        if dev.name == name and evdev.ecodes.KEY_A in caps:
            found.append(dev)
        else:
            dev.close()
    return found, unreadable


def _key_name(evdev, code: int) -> str | None:
    name = evdev.ecodes.KEY.get(code)
    if isinstance(name, list):  # aliases, e.g. ['KEY_MIN_INTERESTING', 'KEY_MUTE']
        name = next((n for n in name if "MIN_" not in n), name[0])
    return name


class Collector:
    def __init__(self, cfg: Config, verbose: bool = False, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.stats = Stats() if dry_run else load(cfg.stats_path)
        self.detector = Detector(self.stats, cfg, emit=self._print if verbose else None)
        self.pause = control.PauseWatcher(cfg)
        self._was_paused = self.pause.paused()
        self._stop = False
        self._flush_now = False
        self._reset_now = False
        self._dirty = False
        self._last_flush = time.monotonic()

    # ----------------------------------------------------------- signals

    def _install_signals(self) -> None:
        def stop(*_):
            self._stop = True

        def flush(*_):
            self._flush_now = True

        def reset(*_):
            self._reset_now = True

        def toggle(*_):
            control.toggle(self.cfg)
            self.pause.invalidate()

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGHUP, flush)
        signal.signal(signal.SIGUSR1, toggle)
        signal.signal(signal.SIGUSR2, reset)

    @staticmethod
    def _print(category: str, detail: str) -> None:
        print(f"{category}: {detail}", flush=True)

    # ------------------------------------------------------------- loop

    def run(self) -> int:
        evdev = _evdev()
        if control.running_pid(self.cfg):
            log(f"already running (pid {control.running_pid(self.cfg)})")
            return 1
        self._install_signals()
        if not self.dry_run:
            control.write_pid(self.cfg)
        mode = " (dry run: nothing is written)" if self.dry_run else ""
        log(f"collecting from {self.cfg.device_name!r}{mode}; stats: {self.cfg.stats_path}")
        if self._was_paused:
            log("paused (keystats resume to start collecting)")
        try:
            waiting_logged = False
            while not self._stop:
                devices, unreadable = find_keyboards(self.cfg.device_name)
                if not devices:
                    if not waiting_logged:
                        hint = (f" ({unreadable} input devices unreadable — see README 'Permissions')"
                                if unreadable else "")
                        log(f"waiting for keyboard {self.cfg.device_name!r}{hint}")
                        waiting_logged = True
                    self._sleep(RETRY_SECONDS)
                    continue
                waiting_logged = False
                log("connected: " + ", ".join(d.path for d in devices))
                self._read(evdev, devices)
                for d in devices:
                    try:
                        d.close()
                    except OSError:
                        pass
                self.detector.reset_window()
        finally:
            self.detector.flush_pending(time.time())
            self._flush(force=True)
            if not self.dry_run:
                control.remove_pid(self.cfg)
            log("stopped")
        return 0

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self._stop and time.monotonic() < end:
            time.sleep(0.2)
            self._housekeeping()

    def _read(self, evdev, devices) -> None:
        by_fd = {d.fd: d for d in devices}
        EV_KEY = evdev.ecodes.EV_KEY
        while not self._stop:
            try:
                ready, _, _ = select.select(list(by_fd), [], [], 1.0)
                for fd in ready:
                    for ev in by_fd[fd].read():
                        if ev.type != EV_KEY or self._check_paused():
                            continue
                        name = _key_name(evdev, ev.code)
                        if name:
                            self.detector.feed(name, ev.value, ev.timestamp())
                            self._dirty = True
            except OSError as e:
                if e.errno in (errno.ENODEV, errno.EIO, errno.EBADF):
                    log("keyboard disconnected; waiting for it to come back")
                    return
                raise
            self._housekeeping()

    def _check_paused(self) -> bool:
        paused = self.pause.paused()
        if paused != self._was_paused:
            self._was_paused = paused
            # Resolve, then discard, the in-memory window on every transition.
            self.detector.flush_pending(time.time())
            self.detector.reset_window()
            log("paused" if paused else "resumed")
        return paused

    def _housekeeping(self) -> None:
        self._check_paused()
        self.detector.tick(time.time())
        if self._reset_now:
            self._reset_now = False
            self.stats = Stats()
            self.detector.stats = self.stats
            self.detector.reset_window()
            self._dirty = True
            self._flush(force=True)
            log("stats reset")
        if self._flush_now:
            self._flush_now = False
            self._flush(force=True)
        elif time.monotonic() - self._last_flush >= self.cfg.flush_interval:
            self._flush()

    def _flush(self, force: bool = False) -> None:
        self._last_flush = time.monotonic()
        if self.dry_run or not (self._dirty or force):
            return
        save(self.stats, self.cfg.stats_path)
        self._dirty = False


def run(cfg: Config, verbose: bool = False, dry_run: bool = False) -> int:
    if not sys.platform.startswith("linux"):
        sys.exit("keystats: Linux only")
    os.umask(0o077)
    return Collector(cfg, verbose=verbose, dry_run=dry_run).run()
