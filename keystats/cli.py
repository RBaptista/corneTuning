"""Command-line interface."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime

from . import __version__, control
from .config import config_path, load_config
from .stats import load, since_date


def _duration(spec: str) -> float:
    units = {"s": 1, "m": 60, "h": 3600}
    spec = spec.strip().lower()
    if spec and spec[-1] in units and spec[:-1].isdigit():
        return int(spec[:-1]) * units[spec[-1]]
    if spec.isdigit():
        return int(spec) * 60
    raise argparse.ArgumentTypeError(f"invalid duration {spec!r}; use e.g. 30s, 10m, 1h")


def cmd_run(cfg, args) -> int:
    from .collector import run
    if args.device:
        cfg.device_name = args.device
    return run(cfg, verbose=args.verbose or args.dry_run, dry_run=args.dry_run)


def cmd_report(cfg, args) -> int:
    from .report import render
    # Ask a running collector to flush first so the report is current.
    if control.send(cfg, signal.SIGHUP):
        before = _mtime(cfg.stats_path)
        for _ in range(20):
            time.sleep(0.05)
            if _mtime(cfg.stats_path) != before:
                break
    since = None
    if args.since:
        try:
            since = since_date(args.since)
        except ValueError as e:
            sys.exit(f"keystats: {e}")
    print(render(load(cfg.stats_path), cfg, since))
    return 0


def _mtime(path) -> float | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def cmd_pause(cfg, args) -> int:
    control.pause(cfg, args.duration)
    if args.duration:
        until = datetime.fromtimestamp(time.time() + args.duration)
        print(f"paused until {until:%H:%M:%S}")
    else:
        print("paused (keystats resume to continue)")
    return 0


def cmd_resume(cfg, args) -> int:
    control.resume(cfg)
    print("resumed")
    return 0


def cmd_toggle(cfg, args) -> int:
    print("paused" if control.toggle(cfg) else "resumed")
    return 0


def cmd_reset(cfg, args) -> int:
    if not args.yes:
        answer = input(f"Wipe all stats in {cfg.stats_path}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("aborted")
            return 1
    if control.send(cfg, signal.SIGUSR2):
        print("asked the running collector to reset")
    else:
        cfg.stats_path.unlink(missing_ok=True)
        print("stats wiped")
    for leftover in cfg.stats_path.parent.glob(cfg.stats_path.name + ".corrupt-*"):
        leftover.unlink()
    return 0


def cmd_status(cfg, args) -> int:
    pid = control.running_pid(cfg)
    print(f"collector:  {'running (pid %d)' % pid if pid else 'not running'}")
    until = control.paused_until(cfg)
    if until is None:
        state = "collecting" if pid else "not paused"
    elif until == 0:
        state = "PAUSED"
    else:
        state = f"PAUSED until {datetime.fromtimestamp(until):%H:%M:%S}"
    print(f"state:      {state}")
    print(f"device:     {cfg.device_name}")
    print(f"config:     {config_path()}{'' if config_path().exists() else ' (not present, using defaults)'}")
    p = cfg.stats_path
    if p.exists():
        st = p.stat()
        mode = oct(st.st_mode & 0o777)
        dmode = oct(p.parent.stat().st_mode & 0o777)
        warn = "" if (mode, dmode) == ("0o600", "0o700") else "  ⚠ expected 0o600 / dir 0o700"
        stats = load(p)
        first, last = stats.day_range()
        total = stats.aggregate()["total_presses"]
        print(f"stats file: {p} ({st.st_size:,} bytes, mode {mode}, dir {dmode}){warn}")
        print(f"last saved: {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}")
        print(f"window:     {first} → {last}, {total:,} keypresses")
    else:
        print(f"stats file: {p} (none yet)")
    return 0


def cmd_devices(cfg, args) -> int:
    from .collector import list_devices
    unreadable = 0
    for path, name in list_devices():
        if name is None:
            unreadable += 1
            continue
        mark = "  ← configured keyboard" if name == cfg.device_name else ""
        print(f"{path:20} {name}{mark}")
    if unreadable:
        print(f"({unreadable} devices not readable by uid {os.getuid()} — see README 'Permissions')")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="keystats",
                                description="Aggregate keyboard usage & misfire stats (no keystrokes stored).")
    p.add_argument("--version", action="version", version=f"keystats {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="collect in the foreground (Ctrl+C flushes and exits)")
    r.add_argument("-v", "--verbose", action="store_true",
                   help="print detected categories live (never letters typed on their own)")
    r.add_argument("--dry-run", action="store_true", help="like --verbose, but write nothing to disk")
    r.add_argument("--device", help="device name (overrides config)")
    r.set_defaults(func=cmd_run)

    r = sub.add_parser("report", help="print the stats report")
    r.add_argument("--since", metavar="Nd", help="only the last N days, e.g. 7d or 2w")
    r.set_defaults(func=cmd_report)

    r = sub.add_parser("pause", help="stop collecting (e.g. while typing passwords)")
    r.add_argument("--for", dest="duration", type=_duration, metavar="DURATION",
                   help="auto-resume after e.g. 30s, 10m, 1h")
    r.set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="resume collecting").set_defaults(func=cmd_resume)
    sub.add_parser("toggle", help="toggle pause (bind this to a hotkey)").set_defaults(func=cmd_toggle)

    r = sub.add_parser("reset", help="wipe all stats")
    r.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    r.set_defaults(func=cmd_reset)

    sub.add_parser("status", help="collector state, stats file and permissions").set_defaults(func=cmd_status)
    sub.add_parser("devices", help="list input devices to find your keyboard's name").set_defaults(func=cmd_devices)

    args = p.parse_args(argv)
    try:
        cfg = load_config()
    except (ValueError, OSError) as e:
        sys.exit(f"keystats: config error: {e}")
    return args.func(cfg, args)
