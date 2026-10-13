# keystats

Passive keyboard usage and misfire statistics for tuning a QMK/Vial Corne layout
on Linux. It reads `/dev/input/event*` directly through `python-evdev`, so it works
the same on KDE Plasma (Wayland), Hyprland, X11 or a TTY. It **never stores what
you type**: it keeps only counters and histograms.

It answers questions like:

- Which shortcuts do I use enough to deserve a dedicated key? (`Ctrl+Shift+C` 340×)
- Which hand's modifiers do I use? (`LCtrl` vs `RCtrl`)
- Are my home-row mods misfiring? (lone holds, `Shift (F) + O → Backspace`)
- Which keys do I mistype? (Backspace-after rate per key)
- How much do I use each layer (NAV / NUM / SYM / WM)?

## Install

Requires Python ≥ 3.11 and `python-evdev`.

```sh
cd ~/Documents/corneTuning
python3 -m venv .venv
.venv/bin/pip install -e .
mkdir -p ~/.local/bin && ln -sf "$PWD/.venv/bin/keystats" ~/.local/bin/keystats
```

(`pipx install .` works too and also puts `keystats` in `~/.local/bin`. On Arch/CachyOS,
`sudo pacman -S python-evdev` provides the dependency system-wide.)

## Permissions

Input devices are `root:input 0660`, so a normal user can't read them. You have two
options.

### Recommended: a udev rule for only this keyboard

```sh
sudo install -m 644 contrib/70-keystats.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=input
```

Then unplug and replug the keyboard. The rule matches the exact device name
`DH747 W-CORNE` and adds a `uaccess` tag. systemd-logind then gives the user at the
active seat read access to **that one interface**, and nothing else: not the
Corne's Mouse, System Control or Consumer Control interfaces, and not any other
keyboard. Check it with:

```sh
keystats devices        # the Corne should show "← configured keyboard"
getfacl /dev/input/by-id/*W-CORNE*-event-kbd
```

If you change keyboards, edit the name in the rule (and in the config, see below).

### Alternative: join the `input` group

```sh
sudo usermod -aG input "$USER"    # log out and back in
```

This is simpler, but it is a real security trade-off. **Any program running as
your user can then read every keyboard**, including your passwords, which makes
it a keylogger's dream. Prefer the udev rule.

## Usage

```sh
keystats devices                 # list input devices, find your keyboard's name
keystats run --dry-run           # verify: prints detected categories live, writes nothing
keystats run                     # collect in the foreground; Ctrl+C flushes and exits
keystats report                  # full report
keystats report --since 7d       # only the last 7 days (also 2w etc.)
keystats pause                   # stop collecting, e.g. before typing a password
keystats pause --for 5m          # pause that auto-resumes
keystats resume
keystats toggle                  # bind this to a hotkey in KDE / Hyprland
keystats status                  # running? paused? stats file and permissions
keystats reset                   # wipe all stats (asks first; -y to skip)
```

`--verbose` / `--dry-run` print **categories**, never what you're typing: `key: BASE`,
`key: NAV`, `shortcut: Ctrl+C  [LCtrl]`, `lone hold: LShift (180 ms)`,
`misfire: LShift+O → Backspace`.

The running collector also understands signals: `SIGUSR1` toggles pause, `SIGUSR2`
resets, and `SIGHUP` flushes to disk. `keystats report` sends `SIGHUP` first, so the
report is always current.

### Hotkey for pause

- **KDE:** System Settings → Shortcuts → Add New → Command: `keystats toggle`
- **Hyprland:** `bind = SUPER SHIFT, P, exec, keystats toggle`

## Run in the background (systemd --user)

```sh
mkdir -p ~/.config/systemd/user
cp contrib/keystats.service ~/.config/systemd/user/
# edit ExecStart if keystats is not at ~/.local/bin/keystats
systemctl --user daemon-reload
systemctl --user enable --now keystats
journalctl --user -u keystats -f        # connect/disconnect/pause messages only
```

The service waits for the keyboard if it's unplugged at start, and reconnects
when you replug it.

## Configuration

This is optional. Copy `contrib/config.example.toml` to
`~/.config/keystats/config.toml`. The main options:

| option | default | meaning |
|---|---|---|
| `device_name` | `DH747 W-CORNE` | exact evdev name of the main keyboard interface |
| `flush_interval` | `60` | seconds between writes to disk |
| `idle_seconds` | `30` | after this much inactivity the pattern window is discarded |
| `misfire_window_ms` | `1000` | modifier+key, then Backspace within this time = misfire |
| `backspace_after_ms` | `500` | key, then Backspace within this time |
| `backspace_run` | `3` | runs of this many Backspaces are ignored (word deletion) |
| `bigrams` | `true` | collect bigram counts |
| `home_row_mods` | A/S/D/F, J/K/L/; | labels such as `LShift (F)` in the report |
| `sym_keys` | `- = [ ] \ \`` | keycodes counted as SYM |

## What's measured, and how

The firmware decides tap vs. hold before it sends anything, so the computer
only sees the result. A tapped home-row key arrives as a short letter press, and
a held one arrives as a modifier. **Physical hold time of home-row keys can't be
observed**, so keystats doesn't try to measure it.

| stat | definition |
|---|---|
| key frequency | presses per keycode (autorepeat is not counted) |
| shortcuts | non-modifier key pressed while modifiers are held, normalized to `Super+Ctrl+Alt+Shift+Key` order, left/right merged, with a per-side breakdown (`LCtrl+RShift`). Shift + a printable key is typing, not a shortcut, but `Shift+Tab` counts. |
| multi-modifier shortcuts | shortcuts with 2+ modifier kinds: candidates for dedicated keys |
| lone holds | a modifier pressed and released with no non-modifier key in between, counted per side, with a histogram of how long the modifier was down. A short duration (<200 ms) means you only just crossed the tapping term. |
| misfires | a modifier with exactly one key pressed while it was the only modifier, then the modifier released, then a plain Backspace within 1 s with nothing in between. Counted per `(modifier, key)`. |
| Backspace-after | key X whose very next key (ignoring modifiers) is Backspace within 500 ms, unless that Backspace starts a run of 3+ (held Backspace autorepeat also counts as a run) |
| bigrams | consecutive non-modifier keys within 1 s. Pressing a non-Shift shortcut breaks the chain. Pairs only. |
| layer category | `Super+…` → WM; arrows / PgUp / PgDn / Home / End → NAV; Shift+digit and `- = [ ] \ \`` → SYM; digits / F-keys → NUM; everything else → BASE. This is an approximation, because layers are invisible to the host. |
| totals | keypresses, active typing time (gaps longer than `idle_seconds` don't count), date range |

Limitations:

- A modifier used with a mouse click (e.g. Ctrl+click) looks like a lone hold, because only the keyboard is watched.
- Tapping Super on its own to open the launcher also counts as a lone hold. The suggestions mention this.

## Privacy

- Only aggregated counters and histograms are ever written. No raw keystrokes, no
  sequences longer than a bigram, no text. Logs contain only connect, disconnect
  and pause messages.
- In memory, keystats holds only the patterns in progress: currently held modifiers, the previous key
  press, one misfire candidate and one Backspace candidate. Each is dropped as soon as it can no
  longer match, on pause and resume, after `idle_seconds` of inactivity, and when the keyboard
  disconnects.
- Stats live in `~/.local/share/keystats/stats.json` with mode `600`, inside a
  directory with mode `700`. The file is written atomically. Data is bucketed per day
  so `--since` works.
- Note that per-key counts and bigram counts are themselves a statistical
  fingerprint of your typing. They're not reversible to text, but keep the file private.
- `keystats pause` stops collection immediately: events are dropped before they
  reach the detector. Pause state is a flag file, so it survives restarts.
- The device is never grabbed, so typing is unaffected even if keystats hangs or crashes.

## Tests

```sh
python3 -m unittest discover -s tests -t .     # no device or evdev needed
```

The tests feed synthetic event sequences through the detector. They cover
shortcut normalization, left/right merging, lone holds, misfires followed by Backspace,
ignored Backspace runs, autorepeat, bigrams, idle handling, file permissions and
report rendering.

## Manual test checklist

- [ ] `keystats devices` shows the Corne marked `← configured keyboard`.
- [ ] `keystats run --dry-run`: typing prints `key: BASE`, never letters. `Ctrl+C`
      prints `shortcut: Ctrl+C`. Hold F with nothing else → `lone hold: LShift`.
      Hold F, tap O, release, then Backspace → `misfire: LShift+O → Backspace`.
- [ ] Typing is unaffected while keystats runs: no lag, no lost or doubled keys, home-row mods still work.
- [ ] Unplug the keyboard while `keystats run` is running: it logs "disconnected" and doesn't
      crash. Replug it: it logs "connected" and keeps counting.
- [ ] Start `keystats run` with the keyboard unplugged: it logs "waiting", then connects when you plug it in.
- [ ] `keystats pause` → `--dry-run` output stops. `keystats resume` → it starts again.
      `keystats pause --for 30s` resumes by itself.
- [ ] `stat -c '%a %n' ~/.local/share/keystats ~/.local/share/keystats/stats.json`
      shows `700` and `600`. `keystats status` warns if not.
- [ ] `grep -c KEY_ ~/.local/share/keystats/stats.json` shows only counters, and
      no readable text appears in the file.
- [ ] Ctrl+C on `keystats run` flushes: `keystats report` includes the last few seconds.
- [ ] `keystats reset` empties the report, both with the collector running and with it stopped.
- [ ] `systemctl --user status keystats` is active after a reboot/login.
