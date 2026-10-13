"""Detection logic tests using synthetic event sequences (no device needed)."""

import json
import os
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path

from keystats import keys as K
from keystats.config import Config
from keystats.detector import Detector
from keystats.report import render
from keystats.stats import Stats, load, save, since_date

T0 = 1_760_000_000.0  # fixed timestamp; all events are relative to it


class Keyboard:
    """Feeds synthetic evdev events. Times are in milliseconds from T0."""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.stats = Stats()
        self.events: list[tuple[str, str]] = []
        self.det = Detector(self.stats, self.cfg, emit=lambda c, d: self.events.append((c, d)))
        self.t = 0.0

    def _at(self, ms):
        if ms is not None:
            self.t = ms
        return T0 + self.t / 1000

    def down(self, key, at=None):
        self.det.feed(f"KEY_{key}", 1, self._at(at))
        return self

    def up(self, key, at=None):
        self.det.feed(f"KEY_{key}", 0, self._at(at))
        return self

    def repeat(self, key, at=None):
        self.det.feed(f"KEY_{key}", 2, self._at(at))
        return self

    def tap(self, key, at=None, hold=64):
        """A firmware tap: fixed ~64 ms press->release."""
        self.down(key, at)
        self.up(key, self.t + hold)
        return self

    def finish(self):
        self.det.flush_pending(self._at(None))
        return self.stats.aggregate()


class TestShortcuts(unittest.TestCase):
    def test_normalization_and_order(self):
        kb = Keyboard()
        kb.down("RIGHTSHIFT", 0).down("LEFTCTRL", 10).tap("C", 20).up("LEFTCTRL", 120).up("RIGHTSHIFT", 130)
        d = kb.finish()
        self.assertEqual(d["shortcuts"], {"Ctrl+Shift+C": 1})
        self.assertEqual(d["shortcut_sides"], {"Ctrl+Shift+C": {"LCtrl+RShift": 1}})

    def test_super_first(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).down("LEFTMETA", 5).tap("3", 10).up("LEFTMETA", 100).up("LEFTSHIFT", 110)
        d = kb.finish()
        self.assertEqual(d["shortcuts"], {"Super+Shift+3": 1})
        self.assertEqual(d["categories"], {"WM": 1})

    def test_left_right_merged_with_breakdown(self):
        kb = Keyboard()
        kb.down("LEFTCTRL", 0).tap("C", 10).up("LEFTCTRL", 100)
        kb.down("RIGHTCTRL", 1000).tap("C", 1010).up("RIGHTCTRL", 1100)
        kb.down("RIGHTCTRL", 2000).tap("C", 2010).up("RIGHTCTRL", 2100)
        d = kb.finish()
        self.assertEqual(d["shortcuts"], {"Ctrl+C": 3})
        self.assertEqual(d["shortcut_sides"]["Ctrl+C"], {"LCtrl": 1, "RCtrl": 2})
        self.assertEqual(d["mod_usage"], {"LCtrl": 1, "RCtrl": 2})

    def test_both_shifts_merged(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).down("RIGHTSHIFT", 5).down("LEFTCTRL", 8).tap("T", 10)
        _, sided = K.combo(["KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTCTRL"], "KEY_T")
        d = kb.finish()
        self.assertEqual(d["shortcuts"], {"Ctrl+Shift+T": 1})
        self.assertEqual(sided, "LCtrl+LShift+RShift")

    def test_shift_letter_is_typing_not_shortcut(self):
        kb = Keyboard()
        kb.down("RIGHTSHIFT", 0).tap("H", 10).up("RIGHTSHIFT", 100)
        kb.down("LEFTSHIFT", 1000).tap("TAB", 1010).up("LEFTSHIFT", 1100)
        d = kb.finish()
        self.assertEqual(d["shortcuts"], {"Shift+Tab": 1})

    def test_shift_digit_is_sym(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).tap("1", 10).up("LEFTSHIFT", 100).tap("1", 500).tap("UP", 900)
        d = kb.finish()
        self.assertEqual(d["categories"], {"SYM": 1, "NUM": 1, "NAV": 1})


class TestLoneHolds(unittest.TestCase):
    def test_lone_hold_counted(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).up("LEFTSHIFT", 150)
        kb.down("LEFTSHIFT", 1000).up("LEFTSHIFT", 1700)
        d = kb.finish()
        self.assertEqual(d["lone_holds"], {"LShift": 2})
        self.assertEqual(d["lone_hold_ms"], {"LShift": {"100-200ms": 1, "500ms-1s": 1}})
        self.assertIn(("lone hold", "LShift (150 ms)"), kb.events)

    def test_modifier_with_key_is_not_lone(self):
        kb = Keyboard()
        kb.down("LEFTCTRL", 0).tap("C", 10).up("LEFTCTRL", 200)
        self.assertEqual(kb.finish()["lone_holds"], {})

    def test_two_mods_nothing_else_both_lone(self):
        kb = Keyboard()
        kb.down("LEFTCTRL", 0).down("LEFTSHIFT", 20).up("LEFTSHIFT", 200).up("LEFTCTRL", 210)
        self.assertEqual(kb.finish()["lone_holds"], {"LCtrl": 1, "LShift": 1})

    def test_release_without_press_ignored(self):
        kb = Keyboard()
        kb.up("LEFTALT", 0)
        self.assertEqual(kb.finish()["lone_holds"], {})


class TestMisfires(unittest.TestCase):
    def test_mod_key_release_then_backspace(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).tap("O", 50).up("LEFTSHIFT", 200).tap("BACKSPACE", 600)
        d = kb.finish()
        self.assertEqual(d["misfires"], {"LShift+KEY_O": 1})

    def test_backspace_too_late(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).tap("O", 50).up("LEFTSHIFT", 200).tap("BACKSPACE", 1300)
        self.assertEqual(kb.finish()["misfires"], {})

    def test_other_key_in_between(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).tap("O", 50).up("LEFTSHIFT", 200).tap("K", 300).tap("BACKSPACE", 400)
        self.assertEqual(kb.finish()["misfires"], {})

    def test_two_keys_with_mod_not_misfire(self):
        kb = Keyboard()
        kb.down("LEFTCTRL", 0).tap("C", 50).tap("V", 150).up("LEFTCTRL", 300).tap("BACKSPACE", 400)
        self.assertEqual(kb.finish()["misfires"], {})

    def test_multi_mod_combo_not_misfire(self):
        kb = Keyboard()
        kb.down("LEFTCTRL", 0).down("LEFTSHIFT", 10).tap("C", 50)
        kb.up("LEFTSHIFT", 200).up("LEFTCTRL", 210).tap("BACKSPACE", 400)
        self.assertEqual(kb.finish()["misfires"], {})

    def test_ctrl_backspace_not_misfire(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).tap("O", 50).up("LEFTSHIFT", 200)
        kb.down("LEFTCTRL", 300).tap("BACKSPACE", 350).up("LEFTCTRL", 450)
        self.assertEqual(kb.finish()["misfires"], {})


class TestBackspaceAfter(unittest.TestCase):
    def test_single_backspace_counts(self):
        kb = Keyboard()
        kb.tap("E", 0).tap("BACKSPACE", 300).tap("R", 600)
        d = kb.finish()
        self.assertEqual(d["bs_after"], {"KEY_E": 1})

    def test_counted_at_end_of_stream(self):
        kb = Keyboard()
        kb.tap("E", 0).tap("BACKSPACE", 300)
        self.assertEqual(kb.finish()["bs_after"], {"KEY_E": 1})

    def test_two_backspaces_still_count(self):
        kb = Keyboard()
        kb.tap("E", 0).tap("BACKSPACE", 200).tap("BACKSPACE", 400).tap("R", 700)
        self.assertEqual(kb.finish()["bs_after"], {"KEY_E": 1})

    def test_run_of_three_ignored(self):
        kb = Keyboard()
        kb.tap("E", 0).tap("BACKSPACE", 200).tap("BACKSPACE", 350).tap("BACKSPACE", 500).tap("R", 800)
        self.assertEqual(kb.finish()["bs_after"], {})

    def test_held_backspace_autorepeat_is_a_run(self):
        kb = Keyboard()
        kb.tap("E", 0).down("BACKSPACE", 200).repeat("BACKSPACE", 700).repeat("BACKSPACE", 730)
        kb.up("BACKSPACE", 760).tap("R", 1000)
        self.assertEqual(kb.finish()["bs_after"], {})

    def test_outside_window(self):
        kb = Keyboard()
        kb.tap("E", 0).tap("BACKSPACE", 600).tap("R", 900)
        self.assertEqual(kb.finish()["bs_after"], {})

    def test_modifier_between_is_ignored(self):
        kb = Keyboard()
        kb.tap("E", 0).down("LEFTSHIFT", 100).up("LEFTSHIFT", 150).tap("BACKSPACE", 300)
        self.assertEqual(kb.finish()["bs_after"], {"KEY_E": 1})

    def test_tick_resolves_run(self):
        kb = Keyboard()
        kb.tap("E", 0).tap("BACKSPACE", 200)
        kb.det.tick(T0 + 5)
        self.assertEqual(kb.stats.aggregate()["bs_after"], {"KEY_E": 1})


class TestBigramsAndTotals(unittest.TestCase):
    def test_bigrams(self):
        kb = Keyboard()
        kb.tap("T", 0).tap("H", 100).tap("E", 200).tap("X", 2000)
        d = kb.finish()
        self.assertEqual(d["bigrams"], {"KEY_T KEY_H": 1, "KEY_H KEY_E": 1})

    def test_bigrams_skip_shortcuts_but_keep_shift(self):
        kb = Keyboard()
        kb.tap("A", 0).down("LEFTCTRL", 50).tap("C", 100).up("LEFTCTRL", 200).tap("B", 300)
        kb.down("RIGHTSHIFT", 400).tap("D", 450).up("RIGHTSHIFT", 550)
        self.assertEqual(kb.finish()["bigrams"], {"KEY_B KEY_D": 1})

    def test_bigrams_disabled(self):
        cfg = Config()
        cfg.bigrams = False
        kb = Keyboard(cfg)
        kb.tap("T", 0).tap("H", 100)
        self.assertEqual(kb.finish()["bigrams"], {})

    def test_totals_and_active_time_excludes_idle(self):
        kb = Keyboard()
        kb.tap("A", 0).tap("B", 1000).tap("C", 2000).tap("D", 62_000)  # 60 s gap > idle
        d = kb.finish()
        self.assertEqual(d["total_presses"], 4)
        self.assertAlmostEqual(d["active_seconds"], 2.0)
        self.assertEqual(d["keys"], {"KEY_A": 1, "KEY_B": 1, "KEY_C": 1, "KEY_D": 1})

    def test_autorepeat_not_counted_as_press(self):
        kb = Keyboard()
        kb.down("J", 0).repeat("J", 500).repeat("J", 530).up("J", 560)
        self.assertEqual(kb.finish()["total_presses"], 1)

    def test_idle_drops_held_modifiers(self):
        kb = Keyboard()
        kb.down("LEFTCTRL", 0)  # release lost (e.g. unplug)
        kb.tap("C", 60_000)
        self.assertEqual(kb.finish()["shortcuts"], {})


class TestPrivacyAndStorage(unittest.TestCase):
    def test_stats_contain_no_sequences(self):
        kb = Keyboard()
        t = 0
        for ch in "SECRET":
            kb.tap(ch, t)
            t += 120
        blob = json.dumps(kb.finish())
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("KEY_S KEY_E KEY_C", blob)

    def test_save_permissions_and_roundtrip(self):
        kb = Keyboard()
        kb.tap("A", 0).tap("B", 100)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keystats" / "stats.json"
            save(kb.stats, path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
            again = load(path)
            self.assertEqual(again.aggregate(), kb.stats.aggregate())

    def test_corrupt_file_set_aside(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stats.json"
            path.write_text("{not json")
            self.assertEqual(load(path).days, {})
            self.assertEqual(len(list(Path(tmp).glob("stats.json.corrupt-*"))), 1)

    def test_since(self):
        self.assertEqual(since_date("7d", date(2026, 10, 13)), date(2026, 10, 7))
        self.assertEqual(since_date("1w", date(2026, 10, 13)), date(2026, 10, 7))
        with self.assertRaises(ValueError):
            since_date("abc")

    def test_report_renders(self):
        kb = Keyboard()
        kb.down("LEFTSHIFT", 0).up("LEFTSHIFT", 150)
        kb.down("LEFTCTRL", 500).down("LEFTSHIFT", 510).tap("C", 520).up("LEFTSHIFT", 600).up("LEFTCTRL", 610)
        kb.down("LEFTSHIFT", 1000).tap("O", 1010).up("LEFTSHIFT", 1100).tap("BACKSPACE", 1300)
        kb.finish()
        out = render(kb.stats, Config())
        for section in ("Top 30 keys", "Ctrl+Shift+C", "LShift (F)", "Backspace", "Suggestions"):
            self.assertIn(section, out)


if __name__ == "__main__":
    unittest.main()
