"""Editing song sections by hand (deck waveform, MCP).

Sections always tile the track without gaps: [{start, end, group, label, conf, name?}], sorted, the first
starting at 0 and the last ending at the duration. Every function returns a new list and leaves the
argument untouched. Edited sections get conf 1.0 (the user is sure). "name" is an optional free text
that is shown instead of the label (label "custom" when it does not match one of structure.LABELS).
"""
from __future__ import annotations

import string

from .structure import LABELS

MIN_LEN = 1.0          # seconds; no section can be made shorter than this


def _copy(sections: list[dict]) -> list[dict]:
    return [dict(s) for s in sections or []]


def _free_group(sections: list[dict]) -> str:
    used = {s.get("group") for s in sections}
    return next((c for c in string.ascii_uppercase if c not in used), "Z")


def _round(t: float) -> float:
    return round(float(t), 2)


def index_at(sections: list[dict], t: float) -> int | None:
    for i, s in enumerate(sections or []):
        if s["start"] <= t < s["end"]:
            return i
    return len(sections) - 1 if sections and t >= sections[-1]["end"] else None


def create(duration: float, label: str = "verse") -> list[dict]:
    """One section over the whole track (the start for splitting it up)."""
    return [{"start": 0.0, "end": _round(duration), "group": "A", "label": label, "conf": 1.0}]


def split(sections: list[dict], t: float, duration: float | None = None) -> list[dict]:
    """A new section starts at t (the part after t gets a new group and the same name for now)."""
    out = _copy(sections)
    if not out:
        if not duration:
            return out
        out = create(duration)
    i = index_at(out, t)
    if i is None:
        return out
    s = out[i]
    if t - s["start"] < MIN_LEN or s["end"] - t < MIN_LEN:
        return out
    new = dict(s, start=_round(t), group=_free_group(out), conf=1.0)
    s["end"] = _round(t)
    s["conf"] = 1.0
    out.insert(i + 1, new)
    return out


def move_boundary(sections: list[dict], i: int, t: float) -> list[dict]:
    """Move the start of section i (= the end of section i-1) to t, within its neighbours."""
    out = _copy(sections)
    if not 0 < i < len(out):
        return out
    lo, hi = out[i - 1]["start"] + MIN_LEN, out[i]["end"] - MIN_LEN
    if hi < lo:
        return out
    t = _round(min(max(t, lo), hi))
    out[i - 1]["end"] = out[i]["start"] = t
    out[i - 1]["conf"] = out[i]["conf"] = 1.0
    return out


def delete(sections: list[dict], i: int) -> list[dict]:
    """Remove section i: its time goes to the previous section (the first one: to the next)."""
    out = _copy(sections)
    if not 0 <= i < len(out):
        return out
    if len(out) == 1:
        return []
    if i == 0:
        out[1]["start"] = out[0]["start"]
    else:
        out[i - 1]["end"] = out[i]["end"]
    del out[i]
    return out


def rename(sections: list[dict], i: int, label: str | None = None, name: str | None = None) -> list[dict]:
    """Give section i a standard label (structure.LABELS) or a free name. A section that gets the same name
    as others also gets their colour (same part); otherwise it keeps its own or gets a free one."""
    out = _copy(sections)
    if not 0 <= i < len(out):
        return out
    s = out[i]
    name = (name or "").strip()
    if label in LABELS:
        s["label"] = label
        s.pop("name", None)
    elif name:
        s["label"] = "custom"
        s["name"] = name
    else:
        return out
    s["conf"] = 1.0
    key = (s["label"], s.get("name", ""))
    same = [o for j, o in enumerate(out) if j != i and (o["label"], o.get("name", "")) == key]
    if same:
        s["group"] = same[0]["group"]
    elif any(o["group"] == s["group"] for j, o in enumerate(out) if j != i):
        s["group"] = _free_group(out)          # it no longer is the same part as the others of its group
    return out


def display_name(section: dict, tr=None) -> str:
    """Text for the deck: the free name, else the translated label."""
    if section.get("name"):
        return section["name"]
    return tr(f"sec_{section['label']}") if tr else section["label"]
