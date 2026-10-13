# keystats: keyboard usage & misfire stats for a Corne layout

## Goal

A small Linux tool that passively watches my split keyboard and collects **aggregate statistics** (counts and timings only) so I can fine-tune my QMK/Vial layout: which shortcuts deserve a dedicated key, which keys I mistype, and whether my home-row mods misfire.

It must never store what I actually type.

## Environment

- Linux, KDE Plasma on Wayland now, Hyprland later. Must not depend on X11 or the desktop environment.
- Python 3 + `python-evdev`. Read events directly from `/dev/input/event*`.
- Keyboard device name: `DH747 W-CORNE` (the main keyboard interface, not the `Mouse`, `System Control` or `Consumer Control` interfaces). Find it **by name**, never by a fixed `eventN` number. Make the name configurable so it works with a future keyboard.
- Read-only and passive: **do not grab** the device. Typing must be unaffected.
- Handle the keyboard being unplugged and replugged (wait and reconnect, don't crash).

## How my keyboard behaves (important for the logic)

My layout uses home-row mods: A=Super, S=Alt, D=Ctrl, F=Shift and J=Shift, K=Ctrl, L=Alt, ;=Super (tap = letter, hold = modifier). Layers are momentary (NAV arrows, SYM symbols, NUM numbers, a WM layer that sends Super+key shortcuts).

The firmware decides tap vs hold *before* sending anything, so the computer only sees the result:

- A tapped home-row key arrives as a short, fixed-length press (observed ~64 ms press→release), sent only after I physically release it. **Physical hold time of home-row keys is therefore NOT observable.** Don't try to measure it.
- A held home-row key arrives as a normal modifier press (`KEY_LEFTCTRL`, `KEY_RIGHTSHIFT`, etc.).
- Layers are invisible; only the resulting keycode is seen.

## What to collect

All of these are counters and timing histograms kept in memory and flushed to disk.

1. **Key frequency:** press count per keycode.
2. **Shortcut frequency:** every non-modifier key pressed while modifiers are held, recorded as a normalized string such as `Ctrl+Shift+C` or `Super+Shift+3`. Left and right variants are merged, but also keep a left/right breakdown, since it shows which hand's mods I use.
3. **Multi-modifier shortcuts:** shortcuts with 2+ modifiers, ranked. These are the candidates for single dedicated keys on a layer.
4. **Lone modifier holds:** a modifier pressed and released with no other key in between. Count these per modifier. On my layout, these usually mean "I meant to type the letter but held it too long", so it's a misfire signal.
5. **Accidental modifier (misfire proxy):** modifier pressed, exactly one key pressed with it, modifier released, then Backspace within 1000 ms. Count per `(modifier, key)` pair, e.g. `Shift+O → Backspace`.
6. **Backspace-after rate per key:** for each key X, how often the very next key within 500 ms is Backspace, as a count and a percentage of X's presses. Ignore Backspaces that are part of a run of 3+ (deleting a whole word isn't a per-key error).
7. **Bigram counts (optional, on by default):** counts of consecutive non-modifier key pairs pressed within 1 s of each other. Counts only, never sequences longer than 2.
8. **Usage by layer category:** derived from keycodes (arrows/PgUp/PgDn/Home/End = NAV, digits and F-keys = NUM, `Super+…` = WM, symbols = SYM), so I can see how much each layer is used.
9. **Session totals:** total keypresses, active typing time, and stats window start/end dates.

## Privacy rules (strict)

- Never write raw keystrokes, sequences or text to disk or logs. Only aggregated counters and histograms.
- Keep at most the last ~5 events in memory for pattern detection, and discard them continuously.
- Stats file: `~/.local/share/keystats/stats.json`, permissions `600`, directory `700`.
- Provide a **pause toggle** (e.g. `keystats pause` / `keystats resume`, or a signal like `SIGUSR1`) so I can stop collection while typing passwords. Also auto-pause after a configurable idle period, if easy.
- `keystats reset` wipes all stats.

## CLI

- `keystats run`: start collecting (foreground; Ctrl+C flushes and exits).
- `keystats report`: print a readable report in the terminal:
  - top 30 keys
  - top 30 shortcuts, plus a separate ranked list of multi-modifier shortcuts
  - lone modifier holds per modifier
  - top suspected misfires (`modifier+key → Backspace`)
  - top 15 keys by Backspace-after rate (only keys with at least 50 presses)
  - top 20 bigrams
  - usage by layer category
  - a short "suggestions" section, e.g. "Ctrl+Shift+C used 340× → consider a dedicated key on NAV", "Shift (F) lone holds high → consider a longer tapping term or chordal hold"
- `keystats report --since 7d`: optional, only if daily buckets are easy to add.
- `keystats pause` / `resume` / `reset` / `status`.

## Running in the background

- Provide a `systemd --user` service file and install instructions.
- Permissions: document both options. One is adding my user to the `input` group, but note the security trade-off: any program running as me could then read keyboards. The other is a udev rule granting access to only this device. Recommend the udev rule.

## Testing

- Unit tests for the detection logic using **synthetic event sequences** (no real device needed). Cover lone mod holds, a misfire followed by Backspace, Backspace runs being ignored, shortcut normalization, and left/right merging.
- A `--dry-run` or `--verbose` mode that prints detected *categories* live (e.g. "shortcut: Ctrl+C", "lone hold: LShift") without printing letters, so I can verify it works by typing.
- A manual test checklist in the README: plug and unplug the keyboard, pause and resume, check the stats file permissions, confirm typing is unaffected.

## Deliverables

- `keystats/` Python package (or a single script if small), `requirements.txt` or `pyproject.toml`
- `README.md` with setup, permissions, service install, usage and privacy notes
- tests
