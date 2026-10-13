"""Pattern detection over a stream of key events.

The detector turns ``(keyname, value, timestamp)`` events into aggregate
counters in a :class:`Stats`. It holds only a tiny rolling window of state
(currently held modifiers, the previous key press, one pending misfire
candidate and one pending Backspace candidate), all of which is discarded as
soon as it can no longer match a pattern.

``value`` follows evdev: 1 = press, 0 = release, 2 = autorepeat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import keys as K
from .config import Config
from .stats import Stats, hold_bucket, inc, inc2

PRESS, RELEASE, REPEAT = 1, 0, 2

# Backspaces more than this far apart (seconds) belong to separate runs.
BS_RUN_GAP = 1.0

Emit = Callable[[str, str], None]


@dataclass
class _ModHold:
    pressed_at: float
    # Non-modifier keys pressed while this modifier was held:
    # (keyname, number of modifiers held at that moment)
    keys: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class _Prev:
    key: str
    t: float
    typing: bool  # pressed without non-Shift modifiers (eligible for bigrams)


class Detector:
    def __init__(self, stats: Stats, cfg: Config, emit: Emit | None = None) -> None:
        self.stats = stats
        self.cfg = cfg
        self.emit: Emit = emit or (lambda _cat, _detail: None)
        self.reset_window()

    # ------------------------------------------------------------------ state

    def reset_window(self) -> None:
        """Drop all transient pattern state (on pause, idle, disconnect)."""
        self._held: dict[str, _ModHold] = {}
        self._prev: _Prev | None = None
        self._last_press_t: float | None = None
        # (mod short name, keyname, release time) awaiting a Backspace
        self._misfire: tuple[str, str, float] | None = None
        # Backspace-after tracking
        self._bs_candidate: str | None = None
        self._bs_run = 0
        self._bs_last_t = 0.0

    def tick(self, now: float) -> None:
        """Called periodically; expires state that can no longer match."""
        if self._bs_run and now - self._bs_last_t > BS_RUN_GAP:
            self._finish_bs_run(now)
        if self._last_press_t is not None and now - self._last_press_t > self.cfg.idle_seconds:
            self._finish_bs_run(now)
            self.reset_window()

    def flush_pending(self, now: float) -> None:
        """Resolve anything pending (before saving or exiting)."""
        self._finish_bs_run(now)

    # ------------------------------------------------------------------ input

    def feed(self, key: str, value: int, t: float) -> None:
        if not key.startswith("KEY_"):
            return
        if value == REPEAT:
            if key == K.BACKSPACE and self._bs_run and not self._held:
                # Holding Backspace to autorepeat is a deletion run.
                self._bs_run += 1
                self._bs_last_t = t
            return
        if value == RELEASE:
            if K.is_modifier(key):
                self._mod_release(key, t)
            return
        if value != PRESS:
            return

        if self._last_press_t is not None and t - self._last_press_t > self.cfg.idle_seconds:
            self._finish_bs_run(t)
            self.reset_window()

        day = self.stats.day(t)
        if self._last_press_t is not None:
            day["active_seconds"] += t - self._last_press_t
        self._last_press_t = t
        day["total_presses"] += 1
        inc(day["keys"], key)

        if K.is_modifier(key):
            self._held[key] = _ModHold(pressed_at=t)
            return
        self._key_press(day, key, t)

    # ------------------------------------------------------------- internals

    def _key_press(self, day: dict, key: str, t: float) -> None:
        held = list(self._held)
        kinds = {K.MODIFIERS[m][0] for m in held}
        plain_bs = key == K.BACKSPACE and not held
        typing = kinds <= {"Shift"}

        for h in self._held.values():
            h.keys.append((key, len(held)))

        # 5. accidental modifier -> Backspace
        if self._misfire is not None:
            mod, mkey, released = self._misfire
            self._misfire = None
            if plain_bs and t - released <= self.cfg.misfire_window_ms / 1000:
                inc(day["misfires"], f"{mod}+{mkey}")
                self.emit("misfire", f"{mod}+{K.label(mkey)} → Backspace")

        # 6. Backspace-after rate
        if plain_bs:
            if self._bs_run and t - self._bs_last_t <= BS_RUN_GAP:
                self._bs_run += 1
            else:
                self._finish_bs_run(t)
                self._bs_run = 1
                prev = self._prev
                if (prev is not None and prev.key != K.BACKSPACE
                        and t - prev.t <= self.cfg.backspace_after_ms / 1000):
                    self._bs_candidate = prev.key
            self._bs_last_t = t
        else:
            self._finish_bs_run(t)

        # 7. bigrams
        prev = self._prev
        if (self.cfg.bigrams and typing and prev is not None and prev.typing
                and t - prev.t <= self.cfg.bigram_window_ms / 1000):
            inc(day["bigrams"], f"{prev.key} {key}")
        self._prev = _Prev(key, t, typing)

        # 2. shortcuts
        if held and not (kinds == {"Shift"} and key in K.PRINTABLE):
            merged, sided = K.combo(held, key)
            inc(day["shortcuts"], merged)
            inc2(day["shortcut_sides"], merged, sided)
            for m in held:
                inc(day["mod_usage"], K.mod_short(m))
            self.emit("shortcut", f"{merged}  [{sided}]")

        # 8. layer category
        cat = K.category(key, held, self.cfg.sym_keys)
        inc(day["categories"], cat)
        if not held:
            self.emit("key", cat)

    def _mod_release(self, key: str, t: float) -> None:
        hold = self._held.pop(key, None)
        if hold is None:
            return
        short = K.mod_short(key)
        if not hold.keys:
            # 4. lone modifier hold
            day = self.stats.day(t)
            inc(day["lone_holds"], short)
            ms = (t - hold.pressed_at) * 1000
            inc2(day["lone_hold_ms"], short, hold_bucket(ms))
            self.emit("lone hold", f"{short} ({ms:.0f} ms)")
        elif len(hold.keys) == 1 and hold.keys[0][1] == 1:
            # Exactly one key, and this was the only modifier: candidate misfire.
            self._misfire = (short, hold.keys[0][0], t)

    def _finish_bs_run(self, t: float) -> None:
        if self._bs_candidate is not None and self._bs_run < self.cfg.backspace_run:
            day = self.stats.day(t)
            inc(day["bs_after"], self._bs_candidate)
            self.emit("backspace after", K.label(self._bs_candidate))
        self._bs_candidate = None
        self._bs_run = 0
