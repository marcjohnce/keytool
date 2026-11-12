"""Audio type of a track: song, instrumental, acapella, remix, stem, soundtrack, audio book, radio play,
podcast, interview, commercial, sound effect, note.

A track can have several types ("Daylight (Club Remix Instrumental)" is a remix and an instrumental).
In the database they are a comma separated list of codes in a fixed order; in the files they are the tag
AUDIO_TYPE with English names ("Instrumental; Remix"), which takes precedence over the detection.

Detection looks at the version part of the title and the file name - brackets and the part after " - " -
for the usual markers, e.g. "(Instrumental)", "(Instrumental Mix)", "[Acapella]", "(Vocals)",
"(Extended Remix)", "(Club Remix Instrumental)", "Drums Stem". Words that name a kind of recording
("Interview", "Podcast", "Soundtrack", "Hörbuch" ...) count in the whole title, the album, the genre and
the names of the folders the file lies in. Without any marker a track is a song.
"""
from __future__ import annotations

import os
import re

SONG, INSTRUMENTAL, ACAPELLA, REMIX, STEM = "song", "instrumental", "acapella", "remix", "stem"
AUDIOBOOK, RADIOPLAY, PODCAST, INTERVIEW = "audiobook", "radioplay", "podcast", "interview"
SOUNDTRACK, COMMERCIAL, SOUND_EFFECT, NOTE = "soundtrack", "commercial", "sfx", "note"
TYPES = (SONG, INSTRUMENTAL, ACAPELLA, REMIX, STEM, SOUNDTRACK, AUDIOBOOK, RADIOPLAY, PODCAST, INTERVIEW,
         COMMERCIAL, SOUND_EFFECT, NOTE)
TAG_FIELD = "AUDIO_TYPE"
TAG_NAMES = {SONG: "Song", INSTRUMENTAL: "Instrumental", ACAPELLA: "Acapella", REMIX: "Remix", STEM: "Stem",
             SOUNDTRACK: "Soundtrack", AUDIOBOOK: "Audio Book", RADIOPLAY: "Radio Play", PODCAST: "Podcast",
             INTERVIEW: "Interview", COMMERCIAL: "Commercial", SOUND_EFFECT: "Sound Effect", NOTE: "Note"}

_W = r"(?<![a-zäöüß])"
_E = r"(?![a-zäöüß])"


def _rx(words: str) -> re.Pattern:
    return re.compile(_W + "(" + words + ")" + _E, re.I)


# markers inside the version part of a title (brackets / after " - ")
_VERSION: list[tuple[str, re.Pattern]] = [
    (INSTRUMENTAL, _rx(r"instrumental|instr\.?|inst\.?|karaoke|backing ?track|minus ?one|playback|no vocals?|"
                       r"without vocals?|ohne gesang|off ?vocal")),
    (ACAPELLA, _rx(r"a ?cappella|a ?capella|acapella|acappella|vocals? only|vox only|isolated vocals?|"
                   r"vocal (stem|track)")),
    # "(Vocals)" / "(Vocal)" / "(Vox)" alone - not "Vocal Mix", which is a version with vocals
    (ACAPELLA, re.compile(r"^\s*(lead |dry |clean )?(vocals?|vox)\s*$", re.I)),
    (REMIX, _rx(r"remix|rmx|re-?edit|edit|bootleg|re-?work|flip|vip|dub|mash-?up")),
    # "Club Mix", "Vocal Mix", "Kim's Mix" - but not an instrumental, extended, radio or original mix
    (REMIX, re.compile(r"(?<!instrumental )(?<!original )(?<!extended )(?<!radio )(?<!album )(?<!single )"
                       r"(?<!clean )(?<!dirty )(?<!explicit )(?<![a-z])(?<!^)mix(?![a-z])", re.I)),
    (STEM, _rx(r"stems?|drums? only|bass only|(drum|bass|vocal|melody|music|other) (track|stem)")),
    (SOUND_EFFECT, _rx(r"sfx|fx|sound ?effects?|soundeffekte?|ger[äa]usch(e)?|foley|one ?shot|sample")),
    (NOTE, _rx(r"notiz|note|memo|voice ?memo|sprachmemo|sprachnotiz|voice ?note")),
]
# versions that are the song itself and must not count as a remix
_NOT_REMIX = re.compile(r"^\s*(original( mix)?|album( version)?|radio edit|extended( mix)?|single( version)?|"
                        r"remaster(ed)?( \d{4})?|\d{4} remaster(ed)?|live|explicit|clean)\s*$", re.I)

# markers anywhere in the title, album, genre or folder names
_ANYWHERE: list[tuple[str, re.Pattern]] = [
    (AUDIOBOOK, _rx(r"h[öo]rbuch|h[öo]rb[üu]cher|hoerbuch|hoerbuecher|audio ?books?|kapitel \d+|chapter \d+")),
    (RADIOPLAY, _rx(r"h[öo]rspiele?|hoerspiele?|radio ?plays?|radio ?dramas?|audio ?dramas?")),
    (PODCAST, _rx(r"podcasts?")),
    (INTERVIEW, _rx(r"interviews?")),
    (SOUNDTRACK, _rx(r"soundtracks?|filmmusik|o\.s\.t\.?|ost|original (motion picture )?score|"
                     r"(music )?from the (motion picture|film|movie|series)|aus dem film|score")),
    (COMMERCIAL, _rx(r"werbespots?|werbung|commercials?|advert(isement)?s?|radio ?spots?|tv ?spots?")),
    (SOUND_EFFECT, _rx(r"sound ?effects?|soundeffekte?|ger[äa]usche|sfx|foley|one ?shots?|samples?|sample ?packs?")),
    (NOTE, _rx(r"sprachmemos?|sprachnotiz(en)?|voice ?memos?|notiz(en)?")),
]
# too common in song titles ("Samples of Love", "Notes") - only album, genre and folder names count
_NOT_IN_TITLE = {SOUND_EFFECT, NOTE}
_WEAK_IN_TITLE = {"score", "ost"}          # "Final Score", "Ost" (east) in a title is no soundtrack
# default names of recorder apps: "Neue Aufnahme 12", "New Recording 3", "Sprachmemo 001"
_RECORDING = re.compile(r"^\s*(neue aufnahme|new recording|aufnahme|recording|sprachmemo|voice memo)\s*\d*\s*$",
                        re.I)
# podcast episode names: "Folge 12", "Episode 3", "Ep. 41"
_EPISODE = re.compile(_W + r"(folge|episode|ep\.?) ?#?\d+" + _E, re.I)

_BRACKETS = re.compile(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]")


def _version_parts(text: str) -> list[str]:
    parts = [m.group(1) for m in _BRACKETS.finditer(text)]
    rest = _BRACKETS.sub(" ", text)
    if " - " in rest:                          # "Title - Extended Mix" (only the last part is a version)
        parts.append(rest.rsplit(" - ", 1)[1])
    return [p.strip() for p in parts if p.strip()]


def detect(title: str | None, path: str | None = None, album: str | None = None,
           genre: str | None = None) -> list[str]:
    """Types found in title, file name, album, genre and folder names; [SONG] when nothing points elsewhere."""
    found: set[str] = set()
    episode = False
    names = [t for t in (title, os.path.splitext(os.path.basename(path))[0].replace("_", " ") if path else None)
             if t]
    for text in names:
        for part in _version_parts(text):
            if _NOT_REMIX.match(part):
                continue
            for typ, pat in _VERSION:
                if pat.search(part):
                    found.add(typ)
        if _RECORDING.match(text):
            found.add(NOTE)
        if _EPISODE.search(text):
            episode = True
    folders = [f for f in os.path.dirname(path or "").replace("\\", "/").split("/")[-3:] if f]
    context = folders + [t for t in (album, genre) if t]
    for typ, pat in _ANYWHERE:
        if any(pat.search(t) for t in context):
            found.add(typ)
            continue
        if typ in _NOT_IN_TITLE:
            continue
        for t in names:
            m = pat.search(t)
            if m and m.group(0).lower() not in _WEAK_IN_TITLE:
                found.add(typ)
                break
    if episode and not found & {AUDIOBOOK, RADIOPLAY}:      # "Folge 3" of a radio play is no podcast
        found.add(PODCAST)
    return order(found) or [SONG]


def order(types) -> list[str]:
    s = {t for t in (types or []) if t in TYPES}
    return [t for t in TYPES if t in s]


def encode(types) -> str:
    return ",".join(order(types))


def decode(value: str | None) -> list[str]:
    return order((value or "").split(","))


def to_tag(types) -> str:
    """Value of the AUDIO_TYPE tag: English names, "; " between them."""
    return "; ".join(TAG_NAMES[t] for t in order(types))


_ALIASES = {"sample": SOUND_EFFECT, "sound effects": SOUND_EFFECT, "sfx": SOUND_EFFECT, "geräusch": SOUND_EFFECT,
            "audiobook": AUDIOBOOK, "hörbuch": AUDIOBOOK, "hörspiel": RADIOPLAY, "a cappella": ACAPELLA,
            "acappella": ACAPELLA, "a capella": ACAPELLA, "filmmusik": SOUNDTRACK, "film score": SOUNDTRACK,
            "werbespot": COMMERCIAL, "notiz": NOTE}


def from_tag(text: str | None) -> list[str]:
    """Types from an AUDIO_TYPE tag ("Instrumental; Remix", "instrumental,remix" ...); unknown names are dropped."""
    out = []
    for name in re.split(r"[;,/|]", text or ""):
        n = name.strip().lower()
        if not n:
            continue
        if n in TYPES:
            out.append(n)
        elif n in _ALIASES:
            out.append(_ALIASES[n])
        else:
            out += [code for code, tag in TAG_NAMES.items() if tag.lower() == n]
    return order(out)


def parse(value) -> list[str]:
    """Types from user / MCP input: a list or a string, codes or names in any of the tag spellings."""
    if value is None:
        return []
    if isinstance(value, str):
        return from_tag(value)
    return from_tag(";".join(str(v) for v in value))
