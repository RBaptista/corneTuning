"""Aggregated statistics: in-memory model, daily buckets, JSON persistence.

Only counters and histograms live here. Nothing in this module ever sees a
sequence of keys longer than a bigram.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 1

# Lone-hold duration histogram buckets (upper bound in ms, label).
HOLD_BUCKETS = ((100, "<100ms"), (200, "100-200ms"), (500, "200-500ms"), (1000, "500ms-1s"))
HOLD_BUCKET_MAX = ">1s"


def empty_day() -> dict:
    return {
        "total_presses": 0,
        "active_seconds": 0.0,
        "keys": {},            # keyname -> presses
        "shortcuts": {},       # "Ctrl+Shift+C" -> count
        "shortcut_sides": {},  # "Ctrl+Shift+C" -> {"LCtrl+RShift": count}
        "mod_usage": {},       # "LCtrl" -> shortcuts it took part in
        "lone_holds": {},      # "LShift" -> count
        "lone_hold_ms": {},    # "LShift" -> {bucket: count}
        "misfires": {},        # "LShift+KEY_O" -> count
        "bs_after": {},        # keyname -> times followed by a lone Backspace
        "bigrams": {},         # "KEY_A KEY_B" -> count
        "categories": {},      # "NAV" -> presses
    }


def inc(d: dict, key: str, n: int | float = 1) -> None:
    d[key] = d.get(key, 0) + n


def inc2(d: dict, key: str, sub: str, n: int = 1) -> None:
    inner = d.setdefault(key, {})
    inner[sub] = inner.get(sub, 0) + n


def hold_bucket(ms: float) -> str:
    for limit, name in HOLD_BUCKETS:
        if ms < limit:
            return name
    return HOLD_BUCKET_MAX


def merge_into(dst: dict, src: dict) -> dict:
    """Recursively add numeric values of ``src`` into ``dst``."""
    for k, v in src.items():
        if isinstance(v, dict):
            merge_into(dst.setdefault(k, {}), v)
        elif isinstance(v, (int, float)):
            dst[k] = dst.get(k, 0) + v
    return dst


class Stats:
    def __init__(self) -> None:
        self.days: dict[str, dict] = {}
        self.first_seen: float | None = None
        self.last_seen: float | None = None

    def day(self, t: float) -> dict:
        """Bucket for the local calendar day containing timestamp ``t``."""
        if self.first_seen is None:
            self.first_seen = t
        self.last_seen = t
        key = datetime.fromtimestamp(t).date().isoformat()
        bucket = self.days.get(key)
        if bucket is None:
            bucket = self.days[key] = empty_day()
        return bucket

    def aggregate(self, since: date | None = None) -> dict:
        out = empty_day()
        for day, bucket in self.days.items():
            if since is None or date.fromisoformat(day) >= since:
                merge_into(out, bucket)
        return out

    def day_range(self, since: date | None = None) -> tuple[str | None, str | None]:
        days = sorted(d for d in self.days if since is None or date.fromisoformat(d) >= since)
        return (days[0], days[-1]) if days else (None, None)

    # --- persistence -----------------------------------------------------

    def to_json(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "saved_at": time.time(),
            "days": self.days,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Stats":
        s = cls()
        s.first_seen = data.get("first_seen")
        s.last_seen = data.get("last_seen")
        for day, bucket in data.get("days", {}).items():
            s.days[day] = merge_into(empty_day(), bucket)
        return s


def since_date(spec: str, today: date | None = None) -> date:
    """``"7d"`` -> date 6 days before today (so the window covers 7 calendar days)."""
    today = today or date.today()
    spec = spec.strip().lower()
    units = {"d": 1, "w": 7}
    if not spec or spec[-1] not in units or not spec[:-1].isdigit():
        raise ValueError(f"invalid --since value {spec!r}; use e.g. 7d or 2w")
    n = int(spec[:-1]) * units[spec[-1]]
    if n < 1:
        raise ValueError("--since must be at least 1d")
    return today - timedelta(days=n - 1)


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def save(stats: Stats, path: Path) -> None:
    """Atomically write stats with mode 600 inside a mode 700 directory."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(stats.to_json(), f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load(path: Path) -> Stats:
    try:
        with open(path) as f:
            return Stats.from_json(json.load(f))
    except FileNotFoundError:
        return Stats()
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        # Don't destroy data we can't parse; set it aside and start fresh.
        bad = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        os.replace(path, bad)
        return Stats()
