"""Configuration: defaults, optional TOML file, XDG paths."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .keys import DEFAULT_SYM_KEYS


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "keystats"


def config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "keystats" / "config.toml"


def runtime_dir() -> Path:
    rt = os.environ.get("XDG_RUNTIME_DIR")
    return Path(rt) if rt else data_dir()


@dataclass
class Config:
    # Exact evdev device name of the keyboard's main interface.
    device_name: str = "DH747 W-CORNE"
    # Write stats to disk this often (seconds).
    flush_interval: float = 60.0
    # After this many seconds without a key press the in-memory pattern window is
    # discarded, held-modifier state is dropped and active-time stops counting.
    idle_seconds: float = 30.0
    # Pattern windows (milliseconds).
    misfire_window_ms: float = 1000.0
    backspace_after_ms: float = 500.0
    bigram_window_ms: float = 1000.0
    # A run of this many consecutive Backspaces is "deleting a word", not a per-key error.
    backspace_run: int = 3
    bigrams: bool = True
    # Which physical home-row key produces each modifier (used for report labels).
    home_row_mods: dict[str, str] = field(default_factory=lambda: {
        "LSuper": "A", "LAlt": "S", "LCtrl": "D", "LShift": "F",
        "RShift": "J", "RCtrl": "K", "RAlt": "L", "RSuper": ";",
    })
    # Keycodes counted as the SYM layer (besides Shift+digit).
    sym_keys: frozenset[str] = DEFAULT_SYM_KEYS
    stats_path: Path = field(default_factory=lambda: data_dir() / "stats.json")

    @property
    def pause_path(self) -> Path:
        return self.stats_path.parent / "paused"

    @property
    def pid_path(self) -> Path:
        return runtime_dir() / "keystats.pid"


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    cfg = Config()
    if not path.exists():
        return cfg
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    known = {f.name for f in fields(Config)}
    for key, value in raw.items():
        if key not in known:
            raise ValueError(f"{path}: unknown option {key!r}")
        if key == "sym_keys":
            value = frozenset(v if v.startswith("KEY_") else f"KEY_{v.upper()}" for v in value)
        elif key == "stats_path":
            value = Path(value).expanduser()
        elif key == "home_row_mods":
            value = {**cfg.home_row_mods, **value}
        setattr(cfg, key, value)
    return cfg
