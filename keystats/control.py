"""Pause flag, PID file and signalling the running collector.

Signals understood by ``keystats run``:
  SIGUSR1  toggle pause
  SIGUSR2  reset (wipe) all stats
  SIGHUP   flush stats to disk now
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from .config import Config
from .stats import ensure_private_dir


# ----------------------------------------------------------------- pause flag

def pause(cfg: Config, seconds: float | None = None) -> None:
    """Create the pause flag. With ``seconds``, the pause expires on its own."""
    ensure_private_dir(cfg.pause_path.parent)
    expiry = f"{time.time() + seconds:.0f}" if seconds else ""
    fd = os.open(cfg.pause_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(expiry)


def resume(cfg: Config) -> None:
    cfg.pause_path.unlink(missing_ok=True)


def paused_until(cfg: Config) -> float | None:
    """None if not paused; 0 if paused indefinitely; otherwise expiry timestamp.

    An expired timed pause is removed.
    """
    try:
        content = cfg.pause_path.read_text().strip()
    except FileNotFoundError:
        return None
    if not content:
        return 0.0
    try:
        expiry = float(content)
    except ValueError:
        return 0.0
    if expiry <= time.time():
        resume(cfg)
        return None
    return expiry


def is_paused(cfg: Config) -> bool:
    return paused_until(cfg) is not None


def toggle(cfg: Config) -> bool:
    """Toggle pause; returns the new paused state."""
    if is_paused(cfg):
        resume(cfg)
        return False
    pause(cfg)
    return True


class PauseWatcher:
    """Cheap cached check of the pause flag for the hot event loop."""

    def __init__(self, cfg: Config, ttl: float = 0.25) -> None:
        self.cfg = cfg
        self.ttl = ttl
        self._checked = 0.0
        self._state = is_paused(cfg)

    def invalidate(self) -> None:
        self._checked = 0.0

    def paused(self) -> bool:
        now = time.monotonic()
        if now - self._checked >= self.ttl:
            self._checked = now
            self._state = is_paused(self.cfg)
        return self._state


# ------------------------------------------------------------------- pid file

def write_pid(cfg: Config) -> None:
    cfg.pid_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.pid_path.write_text(str(os.getpid()))


def remove_pid(cfg: Config) -> None:
    try:
        if cfg.pid_path.read_text().strip() == str(os.getpid()):
            cfg.pid_path.unlink()
    except FileNotFoundError:
        pass


def running_pid(cfg: Config) -> int | None:
    """PID of a live ``keystats run`` process, or None."""
    try:
        pid = int(cfg.pid_path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return None
    return pid if b"keystats" in cmdline else None


def send(cfg: Config, sig: signal.Signals) -> bool:
    pid = running_pid(cfg)
    if pid is None:
        return False
    os.kill(pid, sig)
    return True
