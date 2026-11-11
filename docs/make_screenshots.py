"""Render the annotated screenshots in docs/screenshots/ from a made-up demo library.

    python docs/make_screenshots.py            (needs the normal requirements; runs without a display)

Everything shown is invented: titles, artists, albums and the abstract cover images are generated here.
The app runs offscreen in English with the dark theme; numbered markers and a legend are drawn on top."""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "docs", "screenshots")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import numpy as np  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPen,  # noqa: E402
                           QPixmap)
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

SR = 11025

# (title, artist, album, year, camelot key, bpm, energy, cues, genre, keywords, duration s)
TRACKS = [
    ("Midnight Avenue", "The Lanterns", "Neon Weather", 2019, "8A", 124.0, 7, 6, "House", ["Peak Time"], 214),
    ("Glass Harbour", "Mara Veil", "Tidal Lines", 2021, "9A", 122.4, 6, 4, "Deep House", ["Warm-up"], 247),
    ("Copper Sky", "Northbound", "Copper Sky", 2018, "8B", 125.0, 8, 8, "Nu Disco", ["Peak Time"], 201),
    ("Low Tide Signals", "Kora", "Dawn Patrol", 2022, "7A", 123.0, 5, 3, "Deep House", [], 268),
    ("Basement Hymn", "Low Ends", "", 2020, "3A", 128.0, 8, 0, "Techno", [], 332),
    ("Orchard Lights", "Selva", "Green Room", 2017, "10B", 118.0, 4, 2, "Downtempo", ["Warm-up"], 289),
    ("Paper Planes at Noon", "The Lanterns", "Neon Weather", 2019, "1A", 126.0, 7, 5, "House", [], 223),
    ("Static Bloom", "Iris Kade", "Static Bloom", 2023, "5A", 130.0, 9, 7, "Techno", ["Peak Time"], 356),
    ("Saltwater Radio", "Mara Veil", "Tidal Lines", 2021, "12B", 120.0, 5, 0, "Nu Disco", [], 241),
    ("Late Bloomers", "Northbound", "", 2016, "2A", 124.0, 6, 4, "House", [], 262),
]
DECK = 0           # Midnight Avenue in the deck
SELECTED = 1       # Glass Harbour selected (details sidebar)


# --------------------------------------------------------------------------- demo files
def _cover(seed: int) -> bytes:
    """Abstract cover: gradient plus a few soft shapes (no text, no real artwork)."""
    rng = np.random.default_rng(seed)
    img = QImage(400, 400, QImage.Format_RGB32)
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
    hue = int(rng.integers(0, 360))
    g = QLinearGradient(0, 0, 400, 400)
    g.setColorAt(0, QColor.fromHsl(hue, 120, 70)); g.setColorAt(1, QColor.fromHsl((hue + 70) % 360, 150, 150))
    p.fillRect(img.rect(), g)
    for _ in range(3):
        c = QColor.fromHsl(int(rng.integers(0, 360)), 110, int(rng.integers(140, 220))); c.setAlpha(150)
        p.setPen(Qt.NoPen); p.setBrush(c)
        r = float(rng.uniform(40, 140))
        p.drawEllipse(QPointF(float(rng.uniform(40, 360)), float(rng.uniform(40, 360))), r, r)
    p.end()
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QBuffer.WriteOnly); img.save(buf, "JPG", 88)
    return bytes(ba)


def _audio(bpm: float, seconds: float, seed: int) -> np.ndarray:
    """Kick, hats and a pad with an intro / breakdown / drop shape, so the waveform looks like music."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR); t = np.arange(n) / SR
    beat = 60.0 / bpm
    y = 0.18 * np.sin(2 * np.pi * 110 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * t / (beat * 16)))
    for k in range(int(seconds / beat)):
        s = int(k * beat * SR); e = min(n, s + int(0.18 * SR))
        tt = np.arange(e - s) / SR
        y[s:e] += 0.9 * np.sin(2 * np.pi * (55 + 90 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 14)
        h = s + int(beat * SR / 2)
        if h + 400 < n:
            y[h:h + 400] += 0.25 * rng.standard_normal(400) * np.exp(-np.arange(400) / 80)
    shape = np.interp(t, [0, 0.12, 0.13, 0.45, 0.47, 0.58, 0.6, 0.9, 1.0 + 1e-9],
                      [0.35, 0.45, 0.9, 0.95, 0.3, 0.35, 1.0, 1.0, 0.4]) if seconds > 30 else np.ones(n)
    if seconds > 30:
        shape = np.interp(t / seconds, [0, 0.12, 0.13, 0.45, 0.47, 0.58, 0.6, 0.9, 1.0],
                          [0.35, 0.45, 0.9, 0.95, 0.3, 0.35, 1.0, 1.0, 0.4])
    return np.clip(y * shape * 0.6, -1, 1)


def build_library(tmp: str):
    from mutagen.id3 import APIC, GEOB, TALB, TDRC, TIT2, TPE1
    from mutagen.wave import WAVE

    from keytool.config import Settings
    from keytool.db import Database
    from keytool.keys import Key
    from keytool.library import Library
    from keytool.serato import Cue, SeratoMarkers, dump_tag_data

    music = os.path.join(tmp, "Music", "Demo")
    os.makedirs(music)
    lib = Library(Database(os.path.join(tmp, "library.sqlite3")), Settings(write_tags=False))
    paths = []
    for i, (title, artist, album, year, cam, bpm, energy, cues, genre, kws, dur) in enumerate(TRACKS):
        path = os.path.join(music, f"{artist} - {title}.wav")
        seconds = dur if i == DECK else 4
        pcm = (_audio(bpm, seconds, i) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
        f = WAVE(path); f.add_tags()
        f.tags.add(TIT2(encoding=3, text=title)); f.tags.add(TPE1(encoding=3, text=artist))
        if album:
            f.tags.add(TALB(encoding=3, text=album))
        f.tags.add(TDRC(encoding=3, text=str(year)))
        if i != 4:                                           # one track without artwork (record placeholder)
            f.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=_cover(i)))
        if cues:
            colors = ["#cc0000", "#cc8800", "#00cc00", "#00cccc", "#0000cc", "#cc00cc", "#88cc00", "#cc0044"]
            marks = SeratoMarkers(cues=[Cue(c, int(seconds * 1000 * (0.06 + 0.88 * c / max(1, cues - 1))) if cues > 1
                                            else 1000, colors[c], "") for c in range(cues)])
            f.tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Markers2",
                            data=dump_tag_data(marks)))
        f.save()
        paths.append(path)
    lib.scan(paths)
    for i, (title, artist, album, year, cam, bpm, energy, cues, genre, kws, dur) in enumerate(TRACKS):
        t = lib.db.get_track_by_path(paths[i])
        lib.db.update_track(t["id"], key=Key.parse(cam).code, key_source="analysis", key_confidence=0.82,
                            bpm=bpm, bpm_source="analysis", bpm_confidence=0.9, energy=energy, status="ok",
                            genre=genre, duration=float(dur), bitrate=1411, album_artist=artist,
                            analyzed_ok_at=1762700000 + i * 3600)
        if kws:
            lib.db.add_keywords([t["id"]], kws)
    # song sections for the deck track (made up to match the demo audio's intro / build / drop shape)
    dur = TRACKS[DECK][-1]
    plan = [(0.0, 0.12, "A", "intro", 0.9), (0.12, 0.30, "B", "buildup", 0.8), (0.30, 0.46, "C", "drop", 0.95),
            (0.46, 0.60, "D", "break", 0.4), (0.60, 0.90, "C", "drop", 0.95), (0.90, 1.0, "E", "outro", 0.85)]
    sections = [dict(start=round(a * dur, 2), end=round(b * dur, 2), group=g, label=lab, conf=c)
                for a, b, g, lab, c in plan]
    lib.db.set_analysis(lib.db.get_track_by_path(paths[DECK])["id"], dict(sections=sections))
    return lib, paths


# --------------------------------------------------------------------------- annotation
ACCENT = QColor("#F0A53A")
INK = QColor("#1A1206")


def _font(size, weight=600):
    f = QFont("Archivo"); f.setPointSizeF(size); f.setWeight(QFont.Weight(weight))
    return f


def annotate(shot: QPixmap, marks: list, name: str, title: str):
    """shot + numbered markers in a margin around it, each with a thin leader line to its target, and a legend.
    marks: [(target point in shot, label, side)] with side "top" | "bottom" | "left" | "right", optionally
    followed by a shift along the margin (px) to keep neighbouring markers apart."""
    m, line_h = 46, 26
    fm = QFontMetrics(_font(10.5, 450))
    text_w = max(fm.horizontalAdvance(mk[1]) for mk in marks) + 40
    W = max(shot.width() + 2 * m, text_w + 2 * m)
    cols = 2 if len(marks) > 4 and W - 2 * m >= 2 * text_w + 24 else 1
    rows = math.ceil(len(marks) / cols)
    legend_h = 52 + rows * line_h + 14
    H = shot.height() + 2 * m + legend_h
    canvas = QPixmap(W, H); canvas.fill(QColor("#10141F"))
    p = QPainter(canvas); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.TextAntialiasing)
    p.drawPixmap(m, m, shot)
    p.setPen(QPen(QColor("#34405A"), 1)); p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(m - 0.5, m - 0.5, shot.width() + 1, shot.height() + 1))
    right_edge = m + shot.width()
    for n, mk in enumerate(marks, 1):
        pt, _, side = mk[:3]
        shift = mk[3] if len(mk) > 3 else 0
        t = QPointF(pt.x() + m, pt.y() + m)
        if side == "top":
            c = QPointF(t.x() + shift, m / 2)
        elif side == "bottom":
            c = QPointF(t.x() + shift, m + shot.height() + m / 2)
        elif side == "left":
            c = QPointF(m / 2, t.y() + shift)
        else:
            c = QPointF(right_edge + m / 2, t.y() + shift)
        pen = QPen(ACCENT, 1.6); p.setPen(pen)
        p.drawLine(c, t)
        p.setPen(QPen(QColor("#10141F"), 2)); p.setBrush(ACCENT)
        p.drawEllipse(t, 4.5, 4.5)
        p.setPen(QPen(QColor("#10141F"), 3)); p.drawEllipse(c, 13, 13)
        p.setPen(INK); p.setFont(_font(10.5, 800))
        p.drawText(QRectF(c.x() - 13, c.y() - 13, 26, 26), Qt.AlignCenter, str(n))
    y0 = 2 * m + shot.height() + 10
    p.setPen(QColor("#ECE6D9")); p.setFont(_font(13, 750))
    p.drawText(QPointF(m, y0 + 6), title)
    col_w = (W - 2 * m) / cols
    for n, mk in enumerate(marks, 1):
        label = mk[1]
        r, c = (n - 1) % rows, (n - 1) // rows
        x, y = m + c * col_w, y0 + 30 + r * line_h
        p.setPen(Qt.NoPen); p.setBrush(ACCENT); p.drawEllipse(QPointF(x + 10, y), 10, 10)
        p.setPen(INK); p.setFont(_font(9, 800))
        p.drawText(QRectF(x, y - 10, 20, 20), Qt.AlignCenter, str(n))
        p.setPen(QColor("#ECE6D9")); p.setFont(_font(10.5, 450))
        p.drawText(QRectF(x + 28, y - 11, col_w - 40, 22), Qt.AlignVCenter | Qt.AlignLeft, label)
    p.end()
    canvas.save(os.path.join(OUT, name))
    print("wrote", name, canvas.width(), "x", canvas.height())


def pos(w: QWidget, root: QWidget, fx=0.5, fy=0.5, dx=0, dy=0) -> QPoint:
    """Point inside widget w (fractions of its size), in root coordinates."""
    tl = w.mapTo(root, QPoint(0, 0))
    return QPoint(int(tl.x() + w.width() * fx + dx), int(tl.y() + w.height() * fy + dy))


def rect(w: QWidget, root: QWidget) -> QRect:
    return QRect(w.mapTo(root, QPoint(0, 0)), w.size())


# --------------------------------------------------------------------------- screens
def main():
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="keytool-shots-")
    os.environ["KEYTOOL_DATA_DIR"] = os.path.join(tmp, "data")
    app = QApplication.instance() or QApplication([])
    from keytool.gui import theme
    theme.load_fonts()
    import keytool.gui.player as pl
    pl.HAVE_MULTIMEDIA = False                                # no audio device needed
    from keytool.i18n import set_language
    set_language("en")
    lib, paths = build_library(tmp)
    lib.settings.language, lib.settings.theme, lib.settings.notation = "en", "dark", "camelot"

    from keytool.gui.main_window import MainWindow
    from keytool.gui.widgets import ID_ROLE, TrackModel
    from keytool.keys import Key
    w = MainWindow(lib)
    w._cue_cancel.set()
    w.resize(1840, 1040)
    w.show(); app.processEvents()
    # filters: two keys on the wheel are a nice demo, but they would hide most demo tracks -> keep the
    # list complete and show the selection on the key-filter close-up instead
    deck_id = lib.db.get_track_by_path(paths[DECK])["id"]
    sel_id = lib.db.get_track_by_path(paths[SELECTED])["id"]
    w.table.sortByColumn(TrackModel.COLUMNS.index("fit"), Qt.AscendingOrder)
    w.load_into_deck(track_id=deck_id)
    for _ in range(80):                                       # cover, cues and waveform load in threads
        app.processEvents()
        if w.player.wave.peaks is not None and w.player.markers is not None and w.player.cover.image is not None:
            break
        time.sleep(0.05)
    pl_ = w.player
    for b in (pl_.play_btn, pl_.prev_btn, pl_.next_btn, pl_.stop_btn, pl_.prev_cue_btn, pl_.next_cue_btn):
        b.setEnabled(True)
    pl_.wave.set_position(0.36)
    dur = TRACKS[DECK][-1] * 1000
    pl_.time.setText(f"{pl.fmt_time(dur * 0.36)} / {pl.fmt_time(dur)}")
    for r in range(w.proxy.rowCount()):
        if w.proxy.index(r, 0).data(ID_ROLE) == sel_id:
            w.table.selectRow(r); break
    app.processEvents()

    # 1) overview ------------------------------------------------------------
    shot = w.grab()
    root = w
    annotate(shot, [
        (pos(w.analyze_btn, root, 0.5, 0.0, 0, 2), "Toolbar: add files / folders, Analyze menu, Karaoke-Version search", "top"),
        (pos(w.player.cover, root, 0.0, 0.5, 2), "Deck: the track you are working with", "left"),
        (pos(w.f_text, root, 0.0, 0.5, 2), "Search and filters (title, artist, album, year, BPM, keywords)", "left"),
        (pos(w.f_keys, root, 0.0, 0.5, 4), "Key filter: the Camelot wheel", "left"),
        (pos(w.table.viewport(), root, 0.45, 0.8), "Track list with key, tempo, fit to the deck and cues", "bottom"),
        (pos(w.details_scroll, root, 1.0, 0.25, -3), "Details: edit tags, keywords, see analysis sources", "right"),
        (pos(w.toolbar.widgetForAction(w.a_details), root, 0.5, 0.0, 0, 2), "Details and Queue toggles, Settings", "top"),
    ], "01-main-window.png", "KeyTool main window")

    # 2) deck ----------------------------------------------------------------
    d = w.player
    shot = d.grab()
    annotate(shot, [
        (pos(d.cover, d, 0.0, 0.5, 2), "Cover artwork (click to enlarge); a record in the key colour if there is none", "left"),
        (pos(d.title, d, 0.0, 0.0, 120, 4), "Title and artist of the loaded track", "top"),
        (pos(d.prev_btn, d, 0.5, 1.0, 0, -1), "Previous / next track", "bottom"),
        (pos(d.prev_cue_btn, d, 0.5, 1.0, 0, -1), "Previous / next cue point (disabled without cues)", "bottom"),
        (pos(d.play_btn, d, 0.5, 1.0, 0, -1), "Play / pause (Space)", "bottom"),
        (pos(d.mute_btn, d, 0.5, 0.0, 0, 1), "Mute and volume", "top"),
        (pos(d.key_chip, d, 0.5, 0.0, 0, 1), "Key of the deck, in the Camelot colour", "top"),
        (pos(d.tp_buttons[1], d, 0.5, 0.0, 0, 1), "Transpose −1 / +1 semitone at the original tempo (middle: original key)", "top"),
        (pos(d.bpm_lbl, d, 1.0, 0.5, -2), "Tempo", "right"),
        (pos(d.wave, d, 0.75, 1.0, 0, -24), "Waveform with Serato cue points: click a flag to jump, drag to move, right-click to add, rename or delete", "bottom"),
        (pos(d.wave, d, 0.02, 1.0, 30, -8), "Song sections: same colour = same part, \"?\" = unsure; click a name to jump, drag a border, right-click to edit", "bottom"),
    ], "02-deck.png", "Deck")

    # 3) key filter ------------------------------------------------------------
    w.f_keys.set_selected([Key.parse("8A").code, Key.parse("9A").code])
    app.processEvents()
    kf = w.f_keys
    box = kf.parentWidget()
    area = rect(kf, box).adjusted(-16, -34, 16, 60)
    shot = box.grab(area)
    off = area.topLeft()
    def kp(cam):
        code = Key.parse(cam).code
        for yy in range(0, kf.height(), 3):
            for xx in range(0, kf.width(), 3):
                if kf._key_at(QPointF(xx, yy)) == code:
                    best = QPoint(xx, yy)
                    return kf.mapTo(box, best) - off
    annotate(shot, [
        (kp("9A"), "Click: select / deselect a key (inner ring = minor)", "left"),
        (kp("11B"), "Outer ring = major", "top"),
        (kf.mapTo(box, QPoint(kf.width() // 2, kf.height() // 2 + 20)) - off,
         "Centre: number selected; click to clear", "top", 40),
        (kp("4B"), "Right-click: with harmonic neighbours; Shift: also ±1 semitone", "right"),
    ], "03-key-filter.png", "Key filter (Camelot wheel)")
    w.f_keys.clear(); app.processEvents()

    # 4) track list + tooltip ---------------------------------------------------
    w.set_details_visible(False)
    w.resize(1500, 960); app.processEvents()
    tv = w.table
    tv.horizontalScrollBar().setValue(0)
    for c in ("genre", "comment", "ext", "bitrate", "status", "analyzed_ok_at", "path", "keywords", "year"):
        tv.setColumnHidden(TrackModel.COLUMNS.index(c), True)
    app.processEvents()
    vp = tv.viewport()
    hdr = tv.horizontalHeader()
    def cell(row, col):
        idx = w.proxy.index(row, TrackModel.COLUMNS.index(col))
        r = tv.visualRect(idx)
        return vp.mapTo(tv, r.center())
    def head(col):
        i = TrackModel.COLUMNS.index(col)
        x = hdr.sectionViewportPosition(i) + hdr.sectionSize(i) // 2
        return hdr.mapTo(tv, QPoint(x, hdr.height() // 2))
    tip_row = 2
    w.track_tip.tip.show_for(w.model.rows[w.proxy.mapToSource(w.proxy.index(tip_row, 1)).row()],
                             vp.mapToGlobal(cell(tip_row, "title")))
    for _ in range(40):
        app.processEvents()
        if w.track_tip.tip.cover.image is not None:
            break
        time.sleep(0.05)
    list_shot = tv.grab()
    tip_shot = w.track_tip.tip.grab()
    tip_at = cell(tip_row, "title") + QPoint(24, 18)
    combo = QPixmap(list_shot.size()); combo.fill(Qt.transparent)
    p = QPainter(combo); p.drawPixmap(0, 0, list_shot); p.drawPixmap(tip_at, tip_shot); p.end()
    last = tv.visualRect(w.proxy.index(w.proxy.rowCount() - 1, 1))
    combo = combo.copy(0, 0, combo.width(), vp.mapTo(tv, last.bottomLeft()).y() + 12)
    w.track_tip.hide()
    def top(col):
        i = TrackModel.COLUMNS.index(col)
        return hdr.mapTo(tv, QPoint(hdr.sectionViewportPosition(i) + hdr.sectionSize(i) // 2, 2))
    list_marks = [
        (top("fit"), "Fits: harmonic match with the deck (filled = same key, half = neighbour)", "top"),
        (top("key"), "Key chip in the Camelot colour", "top"),
        (top("bpm"), "BPM, rounded (exact value in the tooltip)", "top"),
        (top("delta"), "Tempo difference to the deck", "top"),
        (top("cues"), "Number of Serato cue points in the file", "top"),
        (tip_at + QPoint(tip_shot.width() - 2, tip_shot.height() // 2),
         "Hover title, artist or album: cover, album and year", "right"),
    ]
    for c in ("genre", "comment", "ext", "bitrate", "status", "analyzed_ok_at", "path", "keywords", "year"):
        tv.setColumnHidden(TrackModel.COLUMNS.index(c), False)
    w.set_details_visible(True); w.resize(1840, 1040); app.processEvents()
    annotate(combo, list_marks, "04-track-list.png", "Track list")

    # 5) details ----------------------------------------------------------------
    ds = w.details_scroll
    shot = ds.grab()
    det = w.details
    marks = [(QPoint(ds.width() - 60, 22), "Selected track(s); edits apply to all selected tracks", "right")]
    rb = det.retag_btn
    marks.append((rb.mapTo(ds, QPoint(rb.width() - 2, rb.height() // 2)),
                  "Auto re-tag: suggestions from Discogs, MusicBrainz (ISRC) and the file name", "right"))
    for attr, label in (("year", "Year, album artist and the other tags are editable"),
                        ("key", "Key with its source and confidence; harmonic neighbours below"),
                        ("bpm", "Tempo: type it, or halve / double it")):
        wd = getattr(det, attr, None)
        if isinstance(wd, QWidget) and wd.isVisible():
            marks.append((wd.mapTo(ds, QPoint(wd.width() - 10, wd.height() // 2)), label, "right"))
    annotate(shot, marks, "05-details.png", "Details sidebar")

    # 6) Karaoke-Version dialog ---------------------------------------------------
    from keytool.gui.research_dialog import ResearchDialog
    dlg = ResearchDialog(lib.settings, lib, [Key.parse("8A").code], 120, 128, dark=True, notation="camelot", parent=w)
    dlg.show(); app.processEvents()
    shot = dlg.grab()
    annotate(shot, [
        (pos(dlg.wheel, dlg, 0.0, 0.5, 4), "Keys to search for (same wheel as the key filter)", "left"),
        (pos(dlg.bmax, dlg, 1.0, 0.5, -2), "Tempo range", "right"),
        (pos(dlg.semitones, dlg, 0.0, 0.5, 2), "Also keys ±1 semitone (transposable on the site)", "left"),
        (pos(dlg.preview, dlg, 1.0, 0.5, -2), "What the search covers", "right"),
        (pos(dlg.search_btn, dlg, 0.5, 1.0, 0, -2), "Opens the search on karaoke-version.com in your browser", "bottom"),
    ], "06-karaoke-version.png", "Karaoke-Version search")
    dlg.done(0)

    # 7) Mixed In Key import ------------------------------------------------------
    from test_mik_import import _mik_db
    mik = os.path.join(tmp, "MIKStore.db")
    _mik_db(mik, [(pth, TRACKS[i][4], TRACKS[i][5], TRACKS[i][6]) for i, pth in enumerate(paths)]
            + [(os.path.join(tmp, "Music", "New", "Fresh Track.wav"), "6A", 127.0, 7),
               (os.path.join(tmp, "Music", "New", "Second Wind.wav"), "11A", 121.0, 6),
               (os.path.join(tmp, "Gone", "Old Track.wav"), "4B", 110.0, 3),
               (os.path.join(tmp, "Gone", "Older Track.wav"), "2B", 98.0, 2)],
            collections=[("c1", "Peak Time", [0, 2, 7]), ("c2", "Sunday Set", [1, 5])])
    os.makedirs(os.path.join(tmp, "Music", "New"))
    for nm in ("Fresh Track.wav", "Second Wind.wav"):
        with wave.open(os.path.join(tmp, "Music", "New", nm), "wb") as f_:
            f_.setnchannels(1); f_.setsampwidth(2); f_.setframerate(SR); f_.writeframes(b"\0\0" * SR)
    from keytool.gui.mik_dialog import MikImportDialog
    mdlg = MikImportDialog(lib, w, db_file=mik)
    mdlg.file_lbl.setText(r"C:\Users\you\AppData\Local\Mixed In Key\Mixed In Key\11.0\MIKStore.db")
    mdlg.show(); app.processEvents()
    shot = mdlg.grab()
    annotate(shot, [
        (pos(mdlg.file_lbl, mdlg, 0.0, 0.5, -6), "Library found automatically; pick another file if needed", "left"),
        (pos(mdlg.summary, mdlg, 0.0, 0.5, -6), "Preview: matched tracks, moved files, files that are gone", "left"),
        (pos(mdlg.overwrite, mdlg, 1.0, 0.5, -2), "Replace existing values or only fill empty ones", "right"),
        (pos(mdlg.collections, mdlg, 1.0, 0.5, -2), "Mixed In Key collections become keywords", "right"),
        (pos(mdlg.add_missing, mdlg, 1.0, 0.5, -2), "Optionally add tracks that are not in KeyTool yet", "right"),
    ], "07-mixed-in-key-import.png", "Import from Mixed In Key")
    mdlg.reject()

    # 8) settings -----------------------------------------------------------------
    from keytool.gui.settings_dialog import SettingsDialog
    sdlg = SettingsDialog(lib.settings, w)
    sdlg.resize(980, 470)
    sdlg.show(); app.processEvents()
    shot = sdlg.grab()
    nav = sdlg.findChild(QWidget, "settingsNav")
    marks = []
    if nav is not None:
        marks.append((pos(nav, sdlg, 0.0, 0.08, 4), "Sections: display, analysis, files & tags, MCP server", "left"))
    from PySide6.QtWidgets import QLabel
    descs = [l for l in sdlg.findChildren(QLabel) if l.isVisible() and l.objectName() == "dim" and l.text()
             and l.mapTo(sdlg, QPoint(0, 0)).y() > 140]
    if descs:
        desc = max(descs, key=lambda l: l.mapTo(sdlg, QPoint(0, 0)).y())     # last row: free space below
        marks.append((desc.mapTo(sdlg, QPoint(desc.width() // 2, desc.height())),
                      "Every setting explains what it does", "bottom"))
    annotate(shot, marks, "08-settings.png", "Settings")
    sdlg.reject()

    w.player.shutdown(); w.queue.shutdown()


if __name__ == "__main__":
    main()
