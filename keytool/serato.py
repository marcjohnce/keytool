"""Read Serato DJ cue points, saved loops and track colour ("Serato Markers2").

Format (documented by Jan Holthuis, https://github.com/Holzhaus/serato-tags, MIT):

* MP3 / WAV / AIFF: ID3 GEOB frame "Serato Markers2"
* FLAC:  Vorbis comment SERATO_MARKERS_V2, base64 of
         b"application/octet-stream\\0" + b"\\0" + b"Serato Markers2\\0" + <tag data>
* MP4/M4A: atom ----:com.serato.dj:markersv2 (same wrapping as FLAC)
* Ogg Vorbis: comment serato_markers2 (best effort; Serato's Ogg format is not fully documented)

<tag data> = b"\\x01\\x01" + base64 (linefeed every 72 chars, may be 1 char too long) + NUL padding.
Decoded:   b"\\x01\\x01" + entries, each  <ASCII name>\\0 <uint32 BE length> <payload>, end with \\0.

CUE  payload: 00 | index u8 | position ms u32 | 00 | RGB(3) | 00 00 | name UTF-8 \\0
LOOP payload: 00 | index u8 | start ms u32 | end ms u32 | ff ff ff ff | ARGB(4) | 00 | locked u8 | name \\0
COLOR payload: 00 | RGB(3)                 BPMLOCK payload: u8
"""
from __future__ import annotations

import base64
import os
import struct
from dataclasses import dataclass, field

# Serato DJ Pro shows the stored hot-cue colours slightly differently (docs/colors.md)
DISPLAY_COLORS = {
    "#cc0000": "#c02626", "#cc4400": "#db4e27", "#cc8800": "#f8821a", "#cccc00": "#fac313",
    "#88cc00": "#4eb648", "#44cc00": "#006838", "#00cc00": "#1fad26", "#00cc44": "#8dc63f",
    "#00cc88": "#2b3673", "#00cccc": "#1dbebd", "#0088cc": "#0f88ca", "#0044cc": "#16308b",
    "#0000cc": "#173ba2", "#4400cc": "#5c3f97", "#8800cc": "#6823b6", "#cc00cc": "#ce359e",
    "#cc0088": "#dc1d49", "#cc0044": "#c71136",
}

_MIME_HEADER = b"application/octet-stream\x00"


@dataclass
class Cue:
    index: int              # 0-based hot cue slot (Serato shows index + 1)
    position_ms: int
    color: str              # stored colour "#rrggbb"
    name: str = ""

    @property
    def display_color(self) -> str:
        return DISPLAY_COLORS.get(self.color.lower(), self.color)


@dataclass
class Loop:
    index: int
    start_ms: int
    end_ms: int
    color: str
    locked: bool = False
    name: str = ""


@dataclass
class SeratoMarkers:
    cues: list[Cue] = field(default_factory=list)
    loops: list[Loop] = field(default_factory=list)
    track_color: str | None = None
    bpm_locked: bool | None = None

    def __bool__(self) -> bool:
        return bool(self.cues or self.loops)

    def to_dict(self) -> dict:
        return {
            "cues": [{"slot": c.index + 1, "position_ms": c.position_ms, "color": c.display_color, "name": c.name}
                     for c in self.cues],
            "loops": [{"slot": l.index + 1, "start_ms": l.start_ms, "end_ms": l.end_ms, "color": l.color,
                       "locked": l.locked, "name": l.name} for l in self.loops],
            "track_color": self.track_color, "bpm_locked": self.bpm_locked,
        }


# ------------------------------------------------------------------ parsing
def _b64decode_lenient(b64: bytes) -> bytes:
    b64 = b"".join(b64.split())                 # drop linefeeds / whitespace
    b64 = b64.rstrip(b"=")
    if len(b64) % 4 == 1:                       # Serato quirk: one char too many
        b64 += b"A=="
    else:
        b64 += b"=" * (-len(b64) % 4)
    return base64.b64decode(b64)


def _rgb(b: bytes) -> str:
    return "#" + b.hex()


def parse_entries(payload: bytes) -> SeratoMarkers:
    """Parse decoded Markers2 content (starts with 01 01). Unknown entry types are skipped."""
    m = SeratoMarkers()
    if payload[:2] != b"\x01\x01":
        raise ValueError("not a Serato Markers2 payload")
    pos = 2
    n = len(payload)
    while pos < n:
        end = payload.find(b"\x00", pos)
        if end < 0 or end == pos:               # terminating NUL
            break
        name = payload[pos:end].decode("ascii", "replace")
        pos = end + 1
        if pos + 4 > n:
            break
        (length,) = struct.unpack(">I", payload[pos:pos + 4])
        pos += 4
        data = payload[pos:pos + length]
        pos += length
        try:
            if name == "CUE" and len(data) >= 12:
                idx = data[1]
                (ms,) = struct.unpack(">I", data[2:6])
                label = data[12:].split(b"\x00", 1)[0].decode("utf-8", "replace")
                m.cues.append(Cue(idx, ms, _rgb(data[7:10]), label))
            elif name == "LOOP" and len(data) >= 20:
                idx = data[1]
                start, stop = struct.unpack(">II", data[2:10])
                label = data[20:].split(b"\x00", 1)[0].decode("utf-8", "replace")
                m.loops.append(Loop(idx, start, stop, _rgb(data[15:18]), bool(data[19]), label))
            elif name == "COLOR" and len(data) >= 4:
                m.track_color = _rgb(data[1:4])
            elif name == "BPMLOCK" and data:
                m.bpm_locked = bool(data[0])
        except (struct.error, IndexError):
            continue
    m.cues.sort(key=lambda c: c.index)
    m.loops.sort(key=lambda l: l.index)
    return m


def parse_tag_data(data: bytes) -> SeratoMarkers:
    """Parse the raw tag data as stored in the GEOB frame: 01 01 + base64 + NUL padding."""
    if data.startswith(_MIME_HEADER):           # FLAC/MP4 wrapping already decoded
        data = data[len(_MIME_HEADER) + 1:]
        data = data.split(b"\x00", 1)[1] if b"\x00" in data else data
    if data[:2] != b"\x01\x01":
        raise ValueError("unexpected Serato Markers2 header")
    b64 = data[2:].split(b"\x00", 1)[0]
    return parse_entries(_b64decode_lenient(b64))


def parse_vorbis_value(value: str | bytes) -> SeratoMarkers:
    """FLAC SERATO_MARKERS_V2 / MP4 markersv2: base64 of the MIME-wrapped tag data."""
    raw = value.encode("ascii", "ignore") if isinstance(value, str) else bytes(value)
    if raw[:2] == b"\x01\x01":                  # some writers (Ogg) store the tag data directly
        return parse_tag_data(raw)
    return parse_tag_data(_b64decode_lenient(raw))


def count_cues(path: str) -> int:
    """Number of Serato hot cues in a file (0 if none or unreadable)."""
    try:
        return len(read_serato_markers(path).cues)
    except Exception:
        return 0


def read_serato_markers(path: str) -> SeratoMarkers:
    """Read Serato cues/loops from an audio file. Returns an empty SeratoMarkers if there are none."""
    import mutagen

    try:
        f = mutagen.File(path)
    except Exception:
        return SeratoMarkers()
    if f is None or f.tags is None:
        return SeratoMarkers()
    tags = f.tags
    try:
        if hasattr(tags, "getall"):                                   # ID3: MP3, WAV, AIFF
            for frame in tags.getall("GEOB"):
                if frame.desc == "Serato Markers2":
                    return parse_tag_data(frame.data)
            return SeratoMarkers()
        if type(tags).__name__ == "MP4Tags":
            v = tags.get("----:com.serato.dj:markersv2")
            return parse_vorbis_value(bytes(v[0])) if v else SeratoMarkers()
        for key in ("SERATO_MARKERS_V2", "serato_markers_v2", "serato_markers2", "SERATO_MARKERS2"):
            try:
                v = tags.get(key)
            except Exception:
                v = None
            if v:
                return parse_vorbis_value(v[0])
    except (ValueError, base64.binascii.Error):
        pass
    return SeratoMarkers()


# ------------------------------------------------------------------ writing (used by the tests; mirrors
# the reference implementation so the parser can be checked against Serato's exact byte layout)
def dump_entries(m: SeratoMarkers) -> bytes:
    out = [b"\x01\x01"]

    def entry(name: str, data: bytes):
        out.append(name.encode("ascii") + b"\x00" + struct.pack(">I", len(data)) + data)

    if m.track_color:
        entry("COLOR", b"\x00" + bytes.fromhex(m.track_color[1:]))
    for c in m.cues:
        entry("CUE", b"\x00" + bytes([c.index]) + struct.pack(">I", c.position_ms) + b"\x00"
              + bytes.fromhex(c.color[1:]) + b"\x00\x00" + c.name.encode("utf-8") + b"\x00")
    for l in m.loops:
        entry("LOOP", b"\x00" + bytes([l.index]) + struct.pack(">II", l.start_ms, l.end_ms) + b"\xff\xff\xff\xff"
              + b"\x00" + bytes.fromhex(l.color[1:]) + b"\x00" + bytes([1 if l.locked else 0])
              + l.name.encode("utf-8") + b"\x00")
    if m.bpm_locked is not None:
        entry("BPMLOCK", bytes([1 if m.bpm_locked else 0]))
    out.append(b"\x00")
    return b"".join(out)


def dump_tag_data(m: SeratoMarkers) -> bytes:
    b64 = bytearray(base64.b64encode(dump_entries(m)).replace(b"=", b"A"))
    i = 72
    while i < len(b64):
        b64.insert(i, 0x0A)
        i += 73
    data = b"\x01\x01" + bytes(b64)
    return data + b"\x00" * max(1, 470 - len(data))


def dump_vorbis_value(m: SeratoMarkers) -> str:
    wrapped = _MIME_HEADER + b"\x00" + b"Serato Markers2\x00" + dump_tag_data(m)
    b64 = base64.b64encode(wrapped).rstrip(b"=")
    return b"\n".join(b64[i:i + 72] for i in range(0, len(b64), 72)).decode("ascii")
