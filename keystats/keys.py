"""Keycode naming, modifier handling and layer-category classification.

Everything here works on evdev key *names* (e.g. ``"KEY_LEFTCTRL"``) so the
detection logic can be tested without the ``evdev`` package or a device.
"""

from __future__ import annotations

# keyname -> (kind, side)
MODIFIERS: dict[str, tuple[str, str]] = {
    "KEY_LEFTMETA": ("Super", "L"),
    "KEY_RIGHTMETA": ("Super", "R"),
    "KEY_LEFTCTRL": ("Ctrl", "L"),
    "KEY_RIGHTCTRL": ("Ctrl", "R"),
    "KEY_LEFTALT": ("Alt", "L"),
    "KEY_RIGHTALT": ("Alt", "R"),
    "KEY_LEFTSHIFT": ("Shift", "L"),
    "KEY_RIGHTSHIFT": ("Shift", "R"),
}

# Canonical order used when building shortcut strings ("Super+Shift+3", "Ctrl+Shift+C").
MOD_ORDER = ("Super", "Ctrl", "Alt", "Shift")

BACKSPACE = "KEY_BACKSPACE"

_LABELS = {
    "SEMICOLON": ";", "APOSTROPHE": "'", "GRAVE": "`", "COMMA": ",", "DOT": ".",
    "SLASH": "/", "BACKSLASH": "\\", "MINUS": "-", "EQUAL": "=",
    "LEFTBRACE": "[", "RIGHTBRACE": "]", "SPACE": "Space", "ENTER": "Enter",
    "BACKSPACE": "Backspace", "TAB": "Tab", "ESC": "Esc", "DELETE": "Delete",
    "INSERT": "Insert", "PAGEUP": "PgUp", "PAGEDOWN": "PgDn", "HOME": "Home",
    "END": "End", "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
    "CAPSLOCK": "CapsLock", "SYSRQ": "PrintScreen", "PRINT": "PrintScreen",
    "LEFTCTRL": "LCtrl", "RIGHTCTRL": "RCtrl", "LEFTSHIFT": "LShift",
    "RIGHTSHIFT": "RShift", "LEFTALT": "LAlt", "RIGHTALT": "RAlt",
    "LEFTMETA": "LSuper", "RIGHTMETA": "RSuper", "MUTE": "Mute",
    "VOLUMEUP": "VolUp", "VOLUMEDOWN": "VolDown", "COMPOSE": "Menu",
}

NAV_KEYS = frozenset(
    f"KEY_{k}" for k in ("UP", "DOWN", "LEFT", "RIGHT", "PAGEUP", "PAGEDOWN", "HOME", "END")
)
DIGIT_KEYS = frozenset(f"KEY_{d}" for d in "0123456789")
NUM_KEYS = (
    DIGIT_KEYS
    | frozenset(f"KEY_F{n}" for n in range(1, 25))
    | frozenset(f"KEY_KP{d}" for d in "0123456789")
)
# Symbols that usually live on a SYM layer rather than the Corne base layer.
# Comma, dot, slash, semicolon and apostrophe are base-layer keys on a typical
# Corne, so they're not here. Overridable via config (``sym_keys``).
DEFAULT_SYM_KEYS = frozenset(
    f"KEY_{k}" for k in ("MINUS", "EQUAL", "LEFTBRACE", "RIGHTBRACE", "BACKSLASH", "GRAVE")
)

_LETTERS = frozenset(f"KEY_{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_PUNCT = frozenset(
    f"KEY_{k}" for k in (
        "MINUS", "EQUAL", "LEFTBRACE", "RIGHTBRACE", "BACKSLASH", "GRAVE",
        "SEMICOLON", "APOSTROPHE", "COMMA", "DOT", "SLASH", "SPACE",
    )
)
# Keys that produce a character; Shift + one of these is just typing, not a shortcut.
PRINTABLE = _LETTERS | DIGIT_KEYS | _PUNCT

# Which hand types a key on QWERTY (used only for misfire suggestions).
LEFT_HAND = frozenset(f"KEY_{c}" for c in "QWERTASDFGZXCVB12345") | {"KEY_GRAVE", "KEY_TAB", "KEY_ESC"}
RIGHT_HAND = frozenset(f"KEY_{c}" for c in "YUIOPHJKLNM67890") | {
    "KEY_SEMICOLON", "KEY_APOSTROPHE", "KEY_COMMA", "KEY_DOT", "KEY_SLASH",
    "KEY_MINUS", "KEY_EQUAL", "KEY_LEFTBRACE", "KEY_RIGHTBRACE", "KEY_BACKSLASH",
}


def is_modifier(name: str) -> bool:
    return name in MODIFIERS


def mod_short(name: str) -> str:
    """``KEY_LEFTSHIFT`` -> ``LShift``."""
    kind, side = MODIFIERS[name]
    return side + kind


def label(name: str) -> str:
    """Human-readable label for a key name: ``KEY_C`` -> ``C``, ``KEY_SEMICOLON`` -> ``;``."""
    bare = name[4:] if name.startswith("KEY_") else name
    if bare in _LABELS:
        return _LABELS[bare]
    if len(bare) == 1 or (bare[0] == "F" and bare[1:].isdigit()):
        return bare
    if bare.startswith("KP"):
        return "KP" + bare[2:].capitalize()
    return bare.capitalize()


def combo(held_mods: list[str], key: str) -> tuple[str, str]:
    """Return (merged, sided) shortcut strings.

    ``(["KEY_RIGHTSHIFT", "KEY_LEFTCTRL"], "KEY_C")`` -> ``("Ctrl+Shift+C", "LCtrl+RShift")``.
    """
    order = {k: i for i, k in enumerate(MOD_ORDER)}
    mods = sorted(held_mods, key=lambda m: (order[MODIFIERS[m][0]], MODIFIERS[m][1]))
    kinds: list[str] = []
    for m in mods:
        kind = MODIFIERS[m][0]
        if kind not in kinds:
            kinds.append(kind)
    merged = "+".join(kinds + [label(key)])
    sided = "+".join(mod_short(m) for m in mods)
    return merged, sided


def category(key: str, held_mods: list[str], sym_keys: frozenset[str] = DEFAULT_SYM_KEYS) -> str:
    """Approximate which layer a key press came from (layers are invisible to the host)."""
    kinds = {MODIFIERS[m][0] for m in held_mods}
    if "Super" in kinds:
        return "WM"
    if key in NAV_KEYS:
        return "NAV"
    if key in DIGIT_KEYS and "Shift" in kinds:
        return "SYM"  # Shift+1 = "!" etc.
    if key in NUM_KEYS:
        return "NUM"
    if key in sym_keys:
        return "SYM"
    return "BASE"


def key_hand(key: str) -> str | None:
    if key in LEFT_HAND:
        return "L"
    if key in RIGHT_HAND:
        return "R"
    return None
