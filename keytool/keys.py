"""Musical key representation and notation conversion.

Internally a key is stored as a compact string ``"<pc>:<mode>"`` where ``pc`` is the
pitch class of the tonic (0 = C ... 11 = B) and ``mode`` is ``maj`` or ``min``.
This canonical form is notation-independent and can be rendered as

* Camelot   (8B, 5A, ...)
* Sharps    (C, F#m, ...)
* Flats     (C, Gbm, ...)
* Open Key  (1d, 4m, ...)  - bonus notation used by Traktor
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

NOTATIONS = ("camelot", "sharps", "flats", "openkey")

# languages that name notes in solfège: syllables C..B, sharp suffix, flat suffix, minor, major
_SOLFEGE = {
    "fr": (("do", "ré", "mi", "fa", "sol", "la", "si"), " dièse", " bémol", "mineur", "majeur"),
    "it": (("Do", "Re", "Mi", "Fa", "Sol", "La", "Si"), " diesis", " bemolle", "minore", "maggiore"),
    "es": (("Do", "Re", "Mi", "Fa", "Sol", "La", "Si"), " sostenido", " bemol", "menor", "mayor"),
}

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# Camelot number for each major tonic pitch class (B = major, A = minor)
_CAMELOT_MAJOR = {11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7, 0: 8, 7: 9, 2: 10, 9: 11, 4: 12}
_CAMELOT_MINOR = {8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7, 9: 8, 4: 9, 11: 10, 6: 11, 1: 12}
_CAMELOT_MAJOR_INV = {v: k for k, v in _CAMELOT_MAJOR.items()}
_CAMELOT_MINOR_INV = {v: k for k, v in _CAMELOT_MINOR.items()}

_NOTE_RE = re.compile(
    r"^\s*([A-Ha-h])\s*([#♯b♭]?)[\s\-]*(maj(?:or)?|min(?:or)?|moll|dur|m)?\s*$", re.IGNORECASE
)
_CAMELOT_RE = re.compile(r"^\s*(1[0-2]|0?[1-9])\s*([ABab])\s*$")
_OPENKEY_RE = re.compile(r"^\s*(1[0-2]|0?[1-9])\s*([DMdm])\s*$")
_NATURAL_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11, "H": 11}


@dataclass(frozen=True, order=True)
class Key:
    pc: int        # tonic pitch class 0..11
    minor: bool

    # ------------------------------------------------------------------ factory
    @classmethod
    def from_code(cls, code: str | None) -> "Key | None":
        if not code:
            return None
        try:
            pc, mode = code.split(":")
            return cls(int(pc) % 12, mode == "min")
        except (ValueError, AttributeError):
            return None

    @classmethod
    def parse(cls, text: str | None) -> "Key | None":
        """Parse any common notation: Camelot, Open Key, sharps/flats, 'A minor', 'Am', 'F#m' ..."""
        if not text:
            return None
        t = text.strip()
        if not t:
            return None
        if ":" in t and t.split(":")[0].isdigit():
            return cls.from_code(t)
        m = _CAMELOT_RE.match(t)
        if m:
            n, letter = int(m.group(1)), m.group(2).upper()
            pc = (_CAMELOT_MINOR_INV if letter == "A" else _CAMELOT_MAJOR_INV)[n]
            return cls(pc, letter == "A")
        m = _OPENKEY_RE.match(t)
        if m:
            n, letter = int(m.group(1)), m.group(2).lower()
            camelot_n = ((n + 6) % 12) + 1  # open key 1 == camelot 8
            pc = (_CAMELOT_MINOR_INV if letter == "m" else _CAMELOT_MAJOR_INV)[camelot_n]
            return cls(pc, letter == "m")
        # Normalise "A minor", "a-moll", "Abm", "o" for off-key (ignored)
        t2 = t.replace("♯", "#").replace("♭", "b")
        t2 = re.sub(r"\s+", " ", t2)
        m = _NOTE_RE.match(t2)
        if not m:
            return None
        letter, acc, mode = m.group(1).upper(), m.group(2), (m.group(3) or "")
        pc = _NATURAL_PC[letter]
        if acc == "#":
            pc += 1
        elif acc in ("b",):
            pc -= 1
        minor = mode.lower() in ("m", "min", "minor", "moll")
        # lowercase single letter ("a") is a common minor shorthand
        if not mode and m.group(1).islower() and not acc:
            minor = True
        return cls(pc % 12, minor)

    # ------------------------------------------------------------------ render
    @property
    def code(self) -> str:
        return f"{self.pc}:{'min' if self.minor else 'maj'}"

    @property
    def camelot_number(self) -> int:
        return (_CAMELOT_MINOR if self.minor else _CAMELOT_MAJOR)[self.pc]

    def camelot(self) -> str:
        return f"{self.camelot_number}{'A' if self.minor else 'B'}"

    def openkey(self) -> str:
        n = ((self.camelot_number - 8) % 12) + 1
        return f"{n}{'m' if self.minor else 'd'}"

    def sharps(self) -> str:
        return SHARP_NAMES[self.pc] + ("m" if self.minor else "")

    def flats(self) -> str:
        return FLAT_NAMES[self.pc] + ("m" if self.minor else "")

    def format(self, notation: str = "camelot") -> str:
        return {
            "camelot": self.camelot,
            "sharps": self.sharps,
            "flats": self.flats,
            "openkey": self.openkey,
        }.get(notation, self.camelot)()

    def long_name(self, lang: str = "en", flats: bool | None = None) -> str:
        if flats is None:  # conventional spelling: Bb major, Eb minor, F# minor ...
            flats = self.pc in ((1, 3, 8, 10) if not self.minor else (3, 10))
        name = (FLAT_NAMES if flats else SHARP_NAMES)[self.pc]
        if lang == "de":
            # German note names: H instead of B, -is/-es suffixes
            de = {"B": "H", "Bb": "B", "C#": "Cis", "D#": "Dis", "F#": "Fis", "G#": "Gis", "A#": "Ais",
                  "Db": "Des", "Eb": "Es", "Gb": "Ges", "Ab": "As"}
            name = de.get(name, name)
            if self.minor:
                name = name.lower()
            return f"{name}-{'Moll' if self.minor else 'Dur'}"
        if lang in _SOLFEGE:
            # solfège names: do ré mi … (fr, lower case) / Do Re Mi … (it, es)
            syll, sharp, flat, minor, major = _SOLFEGE[lang]
            base = syll["CDEFGAB".index(name[0])] + ({"#": sharp, "b": flat}[name[1]] if len(name) > 1 else "")
            return f"{base} {minor if self.minor else major}"
        return f"{name} {'minor' if self.minor else 'major'}"

    # ------------------------------------------------------------------ harmony
    def relative(self) -> "Key":
        return Key((self.pc + 3) % 12, False) if self.minor else Key((self.pc - 3) % 12, True)

    def compatible(self) -> list["Key"]:
        """Harmonically compatible keys (Camelot wheel): same, +-1, relative."""
        n = self.camelot_number
        inv = _CAMELOT_MINOR_INV if self.minor else _CAMELOT_MAJOR_INV
        out = [self]
        for d in (-1, 1):
            out.append(Key(inv[((n - 1 + d) % 12) + 1], self.minor))
        out.append(self.relative())
        return out

    def transpose(self, semitones: int) -> "Key":
        return Key((self.pc + semitones) % 12, self.minor)


def all_keys() -> list[Key]:
    """All 24 keys sorted by Camelot wheel order (1A, 1B, 2A, ...)."""
    keys = [Key(pc, mn) for pc in range(12) for mn in (False, True)]
    return sorted(keys, key=lambda k: (k.camelot_number, not k.minor))


def format_code(code: str | None, notation: str = "camelot") -> str:
    k = Key.from_code(code)
    return k.format(notation) if k else ""


def parse_many(texts: Iterable[str]) -> list[Key]:
    out = []
    for t in texts:
        k = Key.parse(t)
        if k:
            out.append(k)
    return out
