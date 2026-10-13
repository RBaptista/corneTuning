"""Render aggregated stats as a terminal report."""

from __future__ import annotations

from datetime import date, datetime

from . import keys as K
from .config import Config
from .stats import HOLD_BUCKET_MAX, HOLD_BUCKETS, Stats

MIN_PRESSES_FOR_BS_RATE = 50


def _top(d: dict, n: int) -> list[tuple[str, float]]:
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "    -"


def _bar(value: float, peak: float, width: int = 20) -> str:
    return "█" * max(1, round(width * value / peak)) if peak else ""


def _mod_label(mod: str, cfg: Config) -> str:
    hr = cfg.home_row_mods.get(mod)
    return f"{mod} ({hr})" if hr else mod


def _table(title: str, rows: list[tuple[str, ...]], empty: str = "no data yet") -> list[str]:
    out = ["", title, "─" * len(title)]
    if not rows:
        return out + [f"  ({empty})"]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        cells = [r[0].ljust(widths[0])] + [c.rjust(w) for c, w in zip(r[1:-1], widths[1:-1])]
        if len(r) > 1:
            cells.append(r[-1])
        out.append("  " + "  ".join(cells).rstrip())
    return out


def render(stats: Stats, cfg: Config, since: date | None = None) -> str:
    d = stats.aggregate(since)
    first, last = stats.day_range(since)
    total = d["total_presses"]
    lines = ["keystats report"]
    if since:
        lines.append(f"window: last {(date.today() - since).days + 1} days (since {since})")
    if first:
        lines.append(f"data from {first} to {last}")
    if stats.last_seen:
        lines.append(f"last key event: {datetime.fromtimestamp(stats.last_seen):%Y-%m-%d %H:%M}")
    hours, rem = divmod(int(d["active_seconds"]), 3600)
    lines.append(f"total keypresses: {total:,}   active typing time: {hours}h {rem // 60:02d}m")
    if not total:
        return "\n".join(lines + ["", "No data yet. Start collecting with: keystats run"])

    # Keys
    top = _top(d["keys"], 30)
    peak = top[0][1] if top else 0
    lines += _table("Top 30 keys", [
        (K.label(k), f"{n:,}", _pct(n, total), _bar(n, peak)) for k, n in top])

    # Shortcuts
    sc = d["shortcuts"]
    sc_total = sum(sc.values())

    def sides(name: str) -> str:
        br = _top(d["shortcut_sides"].get(name, {}), 3)
        return ", ".join(f"{s} {n}" for s, n in br)

    lines += _table("Top 30 shortcuts", [
        (name, f"{n:,}", _pct(n, sc_total), sides(name)) for name, n in _top(sc, 30)])
    multi = {k: v for k, v in sc.items() if k.count("+") >= 2}
    lines += _table("Multi-modifier shortcuts (dedicated-key candidates)", [
        (name, f"{n:,}", sides(name)) for name, n in _top(multi, 30)])
    lines += _table("Modifier usage in shortcuts (which hand)", [
        (_mod_label(m, cfg), f"{n:,}", _pct(n, sum(d["mod_usage"].values())))
        for m, n in _top(d["mod_usage"], 8)])

    # Lone holds
    buckets = [b for _, b in HOLD_BUCKETS] + [HOLD_BUCKET_MAX]
    rows = []
    for mod, n in _top(d["lone_holds"], 8):
        hist = d["lone_hold_ms"].get(mod, {})
        rows.append((_mod_label(mod, cfg), f"{n:,}",
                     "  ".join(f"{b} {hist.get(b, 0)}" for b in buckets)))
    lines += _table("Lone modifier holds (pressed+released, nothing else) — misfire signal", rows)

    # Misfires
    rows = []
    for pair, n in _top(d["misfires"], 20):
        mod, key = pair.split("+", 1)
        rows.append((f"{_mod_label(mod, cfg)} + {K.label(key)} → Backspace", f"{n:,}"))
    lines += _table("Suspected misfires (modifier+key, then Backspace within 1 s)", rows)

    # Backspace-after rate
    rates = [
        (k, d["bs_after"][k], d["keys"].get(k, 0)) for k in d["bs_after"]
        if d["keys"].get(k, 0) >= MIN_PRESSES_FOR_BS_RATE
    ]
    rates.sort(key=lambda r: (-r[1] / r[2], r[0]))
    lines += _table(f"Top 15 keys by Backspace-after rate (≥{MIN_PRESSES_FOR_BS_RATE} presses)", [
        (K.label(k), f"{n:,}/{p:,}", _pct(n, p)) for k, n, p in rates[:15]],
        empty=f"no key with ≥{MIN_PRESSES_FOR_BS_RATE} presses has been followed by Backspace")

    # Bigrams
    if cfg.bigrams or d["bigrams"]:
        bg = d["bigrams"]
        lines += _table("Top 20 bigrams", [
            (" ".join(K.label(k) for k in pair.split(" ")), f"{n:,}", _pct(n, sum(bg.values())))
            for pair, n in _top(bg, 20)])

    # Layers
    cats = d["categories"]
    cat_total = sum(cats.values())
    peak = max(cats.values(), default=0)
    lines += _table("Usage by layer category (approximate)", [
        (c, f"{cats.get(c, 0):,}", _pct(cats.get(c, 0), cat_total), _bar(cats.get(c, 0), peak))
        for c in ("BASE", "NAV", "NUM", "SYM", "WM") if cats.get(c)])

    lines += ["", "Suggestions", "───────────"]
    lines += ["  • " + s for s in suggestions(d, cfg)] or ["  (nothing stands out yet)"]
    return "\n".join(lines)


def suggestions(d: dict, cfg: Config) -> list[str]:
    out: list[str] = []
    total = d["total_presses"]
    sc_total = sum(d["shortcuts"].values())

    # Frequent multi-modifier shortcuts -> dedicated key.
    multi = [(k, v) for k, v in d["shortcuts"].items() if k.count("+") >= 2]
    for name, n in _top(dict(multi), 5):
        if n < 20 and (not sc_total or n / sc_total < 0.02):
            continue
        layer = "WM" if name.startswith("Super+") else "NAV"
        out.append(f"{name} used {n:,}× → consider a dedicated key on {layer}")

    # Lone holds relative to how often the home-row letter itself is typed.
    for mod, n in _top(d["lone_holds"], 8):
        letter = cfg.home_row_mods.get(mod)
        key = _letter_key(letter)
        taps = d["keys"].get(key, 0) if key else 0
        rate = n / (taps + n) if taps + n else 0
        if n < 10 or (taps and rate < 0.01):
            continue
        hist = d["lone_hold_ms"].get(mod, {})
        quick = sum(hist.get(b, 0) for b in ("<100ms", "100-200ms"))
        where = f" ({letter})" if letter else ""
        msg = f"{mod}{where} lone holds high ({n:,}"
        msg += f", {100 * rate:.1f}% of {letter} presses)" if taps else ")"
        msg += " → consider a longer tapping term or chordal hold for this key"
        if quick / n >= 0.5:
            msg += f"; {100 * quick / n:.0f}% were released <200 ms after becoming a hold (near-miss taps)"
        if "Super" in mod:
            msg += " — ignore if you deliberately tap Super for the launcher"
        out.append(msg)

    # Misfires: same-hand vs cross-hand.
    for pair, n in _top(d["misfires"], 3):
        if n < 5:
            continue
        mod, key = pair.split("+", 1)
        hand = K.key_hand(key)
        lbl = f"{_mod_label(mod, cfg)} + {K.label(key)}"
        if hand == mod[0]:
            out.append(f"{lbl} → Backspace {n}× (same-hand roll) → chordal hold would block this")
        else:
            out.append(f"{lbl} → Backspace {n}× (cross-hand) → raise tapping term / disable "
                       "permissive hold for this key, or try flow tap")

    # Keys often followed by Backspace.
    for k, n in d["bs_after"].items():
        p = d["keys"].get(k, 0)
        if p >= MIN_PRESSES_FOR_BS_RATE and n / p >= 0.08 and not K.is_modifier(k):
            out.append(f"{K.label(k)} is followed by Backspace {100 * n / p:.0f}% of the time "
                       "→ check its position/finger")

    # Layer balance.
    cats = d["categories"]
    cat_total = sum(cats.values())
    if cat_total and cats.get("NAV", 0) / cat_total > 0.10:
        out.append(f"NAV is {100 * cats['NAV'] / cat_total:.0f}% of key presses → make sure the NAV "
                   "layer key is on your strongest thumb")
    if total and not out:
        out.append("nothing stands out yet — collect more data")
    return out


def _letter_key(letter: str | None) -> str | None:
    if not letter:
        return None
    if letter.isalpha() and len(letter) == 1:
        return f"KEY_{letter.upper()}"
    return {";": "KEY_SEMICOLON", "'": "KEY_APOSTROPHE", ",": "KEY_COMMA",
            ".": "KEY_DOT", "/": "KEY_SLASH"}.get(letter)
