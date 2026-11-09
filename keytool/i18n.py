"""Very small translation layer (German / English)."""
from __future__ import annotations

LANGUAGES = {"de": "Deutsch", "en": "English"}

_T: dict[str, tuple[str, str]] = {
    # key: (de, en)
    "app_title": ("KeyTool – Tonart & BPM", "KeyTool – Key & BPM"),
    "add_files": ("Dateien hinzufügen", "Add files"),
    "add_folder": ("Ordner hinzufügen", "Add folder"),
    "analyze_pending": ("Ausstehende analysieren", "Analyze pending"),
    "reanalyze": ("Erneut analysieren", "Re-analyze"),
    "write_tags": ("Tags in Datei schreiben", "Write tags to file"),
    "remove": ("Aus Bibliothek entfernen", "Remove from library"),
    "cleanup": ("Fehlende Dateien entfernen", "Remove missing files"),
    "settings": ("Einstellungen", "Settings"),
    "theme_light": ("Heller Modus", "Light mode"),
    "theme_dark": ("Dunkler Modus", "Dark mode"),
    "language": ("Sprache", "Language"),
    "notation": ("Notation", "Notation"),
    "n_camelot": ("Camelot", "Camelot"),
    "n_sharps": ("Kreuze (♯)", "Sharps (♯)"),
    "n_flats": ("Bs (♭)", "Flats (♭)"),
    "n_openkey": ("Open Key", "Open Key"),
    # filter panel
    "filters": ("Suche & Filter", "Search & filter"),
    "search_all": ("Alles durchsuchen …", "Search everything …"),
    "title": ("Titel", "Title"),
    "artist": ("Interpret", "Artist"),
    "album": ("Album", "Album"),
    "year": ("Jahr", "Year"),
    "bpm": ("BPM", "BPM"),
    "key": ("Tonart", "Key"),
    "keywords": ("Schlüsselwörter", "Keywords"),
    "genre": ("Genre", "Genre"),
    "duration": ("Dauer", "Duration"),
    "format": ("Format", "Format"),
    "status": ("Status", "Status"),
    "path": ("Pfad", "Path"),
    "confidence": ("Sicherheit", "Confidence"),
    "from": ("von", "from"),
    "to": ("bis", "to"),
    "match_all": ("alle", "all"),
    "match_any": ("mindestens eins", "any"),
    "reset_filters": ("Filter zurücksetzen", "Reset filters"),
    "harmonic": ("Harmonisch passend zur Auswahl", "Harmonic match for selection"),
    "harmonic_tip": ("Zeigt Titel, die nach dem Camelot-Rad zur gewählten Tonart passen (±6 % BPM)",
                     "Shows tracks that fit the selected key on the Camelot wheel (±6 % BPM)"),
    "all_keys": ("Alle", "All"),
    "wheel_hint": ("Klick: Tonart an/aus · Rechtsklick: inkl. harmonischer Nachbarn · Mitte: zurücksetzen",
                   "Click: toggle key · Right-click: with harmonic neighbours · Centre: reset"),
    "no_keywords": ("Noch keine Schlüsselwörter", "No keywords yet"),
    # details
    "details": ("Details", "Details"),
    "no_selection": ("Kein Titel ausgewählt", "No track selected"),
    "n_selected": ("{n} Titel ausgewählt", "{n} tracks selected"),
    "add_keyword": ("Schlüsselwort hinzufügen …", "Add keyword …"),
    "add": ("Hinzufügen", "Add"),
    "remove_kw": ("Entfernen", "Remove"),
    "apply": ("Übernehmen", "Apply"),
    "manual": ("manuell", "manual"),
    "auto": ("automatisch", "auto"),
    "from_tags": ("aus Datei-Tags", "from file tags"),
    "s_use_tags": ("Vorhandene BPM-/Tonart-Tags übernehmen statt zu analysieren",
                   "Use existing BPM/key tags instead of analysing"),
    "s_use_tags_tip": ("Hat eine Datei bereits BPM und Tonart in ihren Tags, werden diese Werte übernommen. "
                       "Fehlt einer der Werte, wird nur dieser per Analyse ergänzt. "
                       "„Erneut analysieren“ analysiert immer.",
                       "If a file already has BPM and key in its tags, those values are used. If one is "
                       "missing, only that one is filled in by analysis. 'Re-analyze' always analyses."),
    "reset_manual": ("Manuelle Werte zurücksetzen", "Reset manual values"),
    "open_folder": ("Im Explorer anzeigen", "Show in Explorer"),
    "copy_path": ("Pfad kopieren", "Copy path"),
    "tag_values": ("In Datei-Tags", "In file tags"),
    # status
    "st_ok": ("analysiert", "analyzed"),
    "st_pending": ("ausstehend", "pending"),
    "st_error": ("Fehler", "error"),
    "tracks_count": ("{shown} von {total} Titeln", "{shown} of {total} tracks"),
    "scanning": ("Suche Audiodateien … {n}", "Scanning audio files … {n}"),
    "analyzing": ("Analysiere {done}/{total}: {name}", "Analyzing {done}/{total}: {name}"),
    "done_msg": ("Fertig: {ok} analysiert, {from_tags} aus Tags übernommen, {errors} Fehler, {unchanged} unverändert",
                 "Done: {ok} analyzed, {from_tags} taken from tags, {errors} errors, {unchanged} unchanged"),
    "cancel": ("Abbrechen", "Cancel"),
    "cancelled": ("Abgebrochen", "Cancelled"),
    "busy": ("Es läuft bereits eine Analyse.", "An analysis is already running."),
    "drop_hint": ("Audiodateien oder Ordner hierher ziehen\n(MP3, WAV, FLAC, OGG, M4A)",
                  "Drop audio files or folders here\n(MP3, WAV, FLAC, OGG, M4A)"),
    "confirm_remove": ("{n} Titel aus der Bibliothek entfernen? (Die Dateien bleiben erhalten.)",
                       "Remove {n} tracks from the library? (Files are kept.)"),
    "removed_missing": ("{n} fehlende Dateien entfernt", "{n} missing files removed"),
    "tags_written": ("Tags in {n} Dateien geschrieben", "Tags written to {n} files"),
    "tag_errors": ("{n} Fehler beim Schreiben", "{n} write errors"),
    # settings dialog
    "s_general": ("Allgemein", "General"),
    "s_analysis": ("Analyse", "Analysis"),
    "s_bpm_range": ("BPM-Bereich (Oktavkorrektur)", "BPM range (octave correction)"),
    "s_bpm_range_tip": ("Erkannte Tempi werden verdoppelt/halbiert, bis sie in diesem Bereich liegen.",
                        "Detected tempi are doubled/halved until they fall into this range."),
    "s_write_tags": ("BPM & Tonart nach der Analyse automatisch in die Dateien schreiben",
                     "Automatically write BPM & key into files after analysis"),
    "s_tag_notation": ("Notation für Tonart-Tag", "Notation for key tag"),
    "s_workers": ("Parallele Analysen (0 = automatisch)", "Parallel analyses (0 = auto)"),
    "s_reanalyze": ("Bereits analysierte Dateien beim Einlesen erneut analysieren",
                    "Re-analyze already analyzed files on import"),
    "s_theme": ("Darstellung", "Appearance"),
    "theme_system": ("System", "System"),
    "s_mcp": ("MCP-Server", "MCP server"),
    "s_mcp_info": ("Für Claude Desktop o. Ä. folgenden Eintrag in die MCP-Konfiguration übernehmen:",
                   "Add this entry to the MCP configuration of Claude Desktop or similar:"),
    "copy": ("Kopieren", "Copy"),
    "ok": ("OK", "OK"),
    "close": ("Schließen", "Close"),
    "select_folder": ("Ordner wählen", "Select folder"),
    "select_files": ("Audiodateien wählen", "Select audio files"),
    "audio_files": ("Audiodateien", "Audio files"),
    "keyword_rename": ("Schlüsselwort umbenennen", "Rename keyword"),
    "keyword_new_name": ("Neuer Name:", "New name:"),
    "rename": ("Umbenennen …", "Rename …"),
    "delete_everywhere": ("Überall entfernen", "Remove everywhere"),
    "filter_by": ("Danach filtern", "Filter by this"),
    "about": ("Über", "About"),
}


class Translator:
    def __init__(self, lang: str = "de"):
        self.lang = lang if lang in LANGUAGES else "de"

    def __call__(self, key: str, **kw) -> str:
        pair = _T.get(key)
        if not pair:
            return key
        s = pair[0] if self.lang == "de" else pair[1]
        return s.format(**kw) if kw else s


tr = Translator()


def set_language(lang: str) -> None:
    tr.lang = lang if lang in LANGUAGES else "de"
