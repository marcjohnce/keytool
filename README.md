# KeyTool – Tonart- & BPM-Analyse mit Musikbibliothek und MCP-Server

KeyTool analysiert Audiodateien (MP3, WAV, FLAC, OGG, M4A), erkennt **Tempo (BPM)** und **Tonart** und legt alles
in einer durchsuchbaren Bibliothek ab. Du kannst nach Titel, Interpret, Album, Jahr, BPM, Tonart und eigenen
**Schlüsselwörtern** suchen und filtern. Über den eingebauten **MCP-Server** kann z. B. Claude Desktop direkt auf
die Bibliothek zugreifen.

## Funktionen

| Bereich | Umfang |
|---|---|
| Formate | MP3, WAV, FLAC, OGG (Vorbis/Opus), M4A/AAC/ALAC – Dekodierung über FFmpeg (in PyAV enthalten) |
| Einlesen | Einzelne Dateien, ganze Ordnerstrukturen (rekursiv), **Drag & Drop aus dem Windows Explorer** |
| Analyse | BPM mit Nachkommastelle und Oktavkorrektur, Tonart (24 Dur/Moll), Sicherheitswert je Ergebnis |
| Notationen | **Camelot** (8A), **Kreuze** (F#m), **Bs** (Gbm), zusätzlich Open Key (1m) |
| Suche | Freitext + Titel, Interpret, Album, Jahr von/bis, BPM von/bis, Tonarten (Mehrfachauswahl), Schlüsselwörter (alle/mindestens eins) |
| Schlüsselwörter | Pro Titel oder für viele Titel gleichzeitig; umbenennen und überall entfernen |
| DJ-Extras | „Harmonisch passend“: Titel, die nach dem Camelot-Rad zur Auswahl passen (±6 % BPM); BPM halbieren/verdoppeln |
| Nur Fehlendes analysieren | Steht nur die Tonart in den Tags, wird nur die BPM analysiert (etwa 20× schneller als die volle Analyse); steht nur die BPM drin, nur die Tonart; stehen beide drin, gar nichts |
| Tags | BPM & Tonart werden nach der Analyse **automatisch in die Datei geschrieben** (ID3 TBPM/TKEY, Vorbis BPM/INITIALKEY, MP4 tmpo/initialkey) – abschaltbar |
| Detailansicht | Titel, Interpret, Album, Genre, Kommentar, Tonart, Energy (1–10) und Tempo bearbeiten – einzeln oder für viele Titel gleichzeitig; Dateityp, Bitrate, Dateigröße, Dauer und Pfad werden angezeigt. Änderungen werden in die Datei-Tags geschrieben (abschaltbar) |
| Manuelle Korrektur | BPM/Tonart überschreiben; manuelle Werte bleiben bei einer erneuten Analyse erhalten |
| Recherche | Titel bei **Karaoke-Version** (karaoke-version.de) nach Tonart(en) und BPM-Bereich suchen; Treffer direkt in KeyTool mit Tonart(en), Tempo, Dauer, Preis und Markierung, ob der Titel schon in der Bibliothek ist |
| Player | Blaues Player-Panel (Farbe der Listenauswahl) mit weißen Knöpfen; Cover (eingebettet oder cover.jpg/folder.jpg im Ordner, sonst Platzhalter), vorheriger/nächster Titel, Play/Pause, Stopp, automatisches Weiterspielen; Wiedergabe des ausgewählten Titels über das Standard-Ausgabegerät des Systems (folgt Gerätewechseln, z. B. Kopfhörer), Lautstärke, Stumm |
| Transponieren | Wiedergabe um einen Halbton höher (+1) oder tiefer (−1) bei unverändertem Tempo (Rubber Band über pedalboard); Positionen und Cue-Punkte bleiben gültig |
| Serato-Cues | Vorhandene Serato-DJ-Hot-Cues (Farbe, Name) und gespeicherte Loops werden aus den Dateien gelesen (MP3, WAV, FLAC, M4A; OGG soweit möglich) und in der Wellenform angezeigt; Klick auf ein Cue-Fähnchen oder Alt+1…8 springt hin |
| Wellenform | Übersicht des ausgewählten Titels über der Trackliste, gefärbt nach Frequenzbändern (Bass blau, Mitten bernstein, Höhen weiß), Klicken/Ziehen zum Spulen, Taktmarken aus der BPM |
| Oberfläche | Deutsch / Englisch, heller und dunkler Modus (oder System), Umschalten ohne Neustart |
| MCP-Server | Vollzugriff: suchen, Details, Schlüsselwörter, Werte setzen, Tags schreiben, Ordner einlesen & analysieren |

## Installation & Start (Windows)

**Variante A – aus dem Quellcode:**
1. Python 3.10–3.13 (64 Bit) von python.org installieren (Haken bei „Add to PATH“).
2. `start.bat` doppelklicken. Beim ersten Start wird eine virtuelle Umgebung eingerichtet (dauert ein paar Minuten).
   Kommen in einer neuen Version Pakete hinzu, installiert `start.bat` sie beim nächsten Start automatisch nach.

**Variante B – eigene .exe bauen:**
1. `build_exe.bat` doppelklicken.
2. Ergebnis: `dist\KeyTool\KeyTool.exe` (Oberfläche) und `dist\KeyTool\KeyTool-MCP.exe` (MCP-Server).
   Den ganzen Ordner `dist\KeyTool` kannst du an einen beliebigen Ort kopieren.

Ein separates FFmpeg ist nicht nötig.

## Bedienung

* **Dateien/Ordner einlesen:** Toolbar oder einfach aus dem Explorer ins Fenster ziehen. Bereits analysierte,
  unveränderte Dateien werden übersprungen. Die Analyse läuft parallel auf mehreren CPU-Kernen.
* **Suchen:** Das Feld „Alles durchsuchen“ (Strg+F) sucht in Titel, Interpret, Album, Dateiname, Genre, Jahr und
  Schlüsselwörtern. Alle Filter links lassen sich kombinieren.
* **Tonart-Filter:** Die farbigen Felder entsprechen dem Camelot-Rad; die Beschriftung folgt der gewählten Notation.
* **Schlüsselwörter:** Titel auswählen (Mehrfachauswahl möglich), rechts eingeben, mehrere mit Komma trennen.
  Ein Doppelklick auf ein Schlüsselwort filtert danach. Rechtsklick in der Filterliste: umbenennen/entfernen.
* **Details bearbeiten:** rechts Felder ändern und „Speichern“ (Strg+S) oder „Verwerfen“. Bei mehreren
  ausgewählten Titeln zeigen unterschiedliche Werte „(verschiedene Werte)“; übernommen werden nur die Felder,
  die du änderst. Wechselst du mit ungespeicherten Änderungen den Titel, fragt KeyTool nach. Energy wird als
  `TXXX:EnergyLevel` (MP3/WAV), `ENERGYLEVEL` (FLAC/OGG) bzw. `EnergyLevel` (M4A) gespeichert; Mixed-In-Key-
  Kommentare wie „8A - Energy 6“ werden beim Einlesen erkannt.
* **Abspielen:** Titel auswählen → Cover, Wellenform und Player oben laden den Titel. Leertaste oder Doppelklick
  startet/pausiert, Klicken oder Ziehen in der Wellenform spult. ⏮/⏭ (Strg+←/→ oder Medientasten) springen
  zum vorherigen/nächsten Titel in der aktuellen Sortierung und Filterung; ⏮ startet nach den ersten 3 Sekunden
  erst den Titel neu. Am Titelende geht es mit dem nächsten weiter, am Listenende hält der Player an. Ein Klick
  aufs Cover zeigt es groß.
* **Transponieren:** Im Player „−1 / 0 / +1“ wählen. Der Titel spielt weiter, während im Hintergrund die
  transponierte Fassung berechnet wird (auf allen CPU-Kernen; Fortschritt neben der Zeitanzeige), dann wird
  nahtlos an derselben Stelle umgeschaltet. Das Tempo bleibt exakt gleich, die Anzeige zeigt die neue Tonart
  (z. B. „8A → 3A (+1)“). Die Einstellung gilt auch für die nächsten Titel; berechnete Fassungen werden
  zwischengespeichert (die letzten 12, im Datenordner unter `transpose_cache`). Die Originaldatei wird nie verändert.
* **Serato-Cues:** Hot Cues erscheinen in der Wellenform als rote Linie mit nummeriertem Fähnchen, gespeicherte
  Loops als hinterlegter Bereich (🔒 = gesperrt). Ein Klick auf Linie oder Fähnchen (oder Alt+1…8) setzt die Wiedergabeposition millisekundengenau auf den Cue – auch
  während der Wiedergabe und während der Titel noch lädt. Tooltips zeigen Name und Zeit. Gelesen wird der Tag „Serato Markers2“ (Formatbeschreibung: Holzhaus/serato-tags);
  KeyTool ändert diese Daten nie – auch nicht beim Schreiben von BPM, Tonart oder Metadaten. Werden während der Wiedergabe Tags in die
  Datei geschrieben, gibt der Player die Datei kurz frei und spielt an derselben Stelle weiter.
* **Recherche bei Karaoke-Version** (🔎 in der Toolbar oder Rechtsklick „Passende Titel bei Karaoke-Version
  suchen“): Tonarten im Camelot-Rad wählen („Harmonisch erweitern“ fügt die Nachbarn hinzu), BPM-Bereich setzen,
  suchen. Vorbelegt wird mit dem aktuellen Tonart-/BPM-Filter oder mit Tonart und BPM (±3 %) des ausgewählten Titels.
  * Gesucht wird einmal je Tonart (20 Treffer pro Seite, „Weitere Treffer laden“), damit jeder Treffer seine
    Tonart hat. Die Trefferliste der Seite enthält kein Tempo; KeyTool liest Tempo („~“ = variabel), alle Tonarten
    (Songs mit Tonartwechsel haben mehrere) und Dauer von den Song-Seiten nach – gedrosselt, und für zwei Wochen
    zwischengespeichert. Abschaltbar im Dialog.
  * ✓ markiert Titel, die (gleicher Titel und Interpret) schon in deiner Bibliothek sind.
  * Doppelklick öffnet die Song-Seite, „Suche im Browser öffnen“ die gleiche Suche auf der Website.
  * Die AGB von Karaoke-Version untersagen Crawling nur für KI-Training, die robots.txt erlaubt die Suchseiten.
    KeyTool ruft nur ab, was du anforderst, identifiziert sich als „KeyTool“ und hält Pausen zwischen Anfragen ein.
* **Analyse-Warteschlange** (☰ Warteschlange in der Toolbar): Alle Analysen – Import, „Ausstehende analysieren“,
  „Erneut analysieren“ – landen in einer Warteschlange, die von oben nach unten abgearbeitet wird (bei mehreren
  parallelen Analysen starten immer die obersten wartenden Titel; mit „Parallele Analysen = 1“ streng nacheinander).
  * **Jetzt analysieren** (Rechtsklick in der Liste oder in der Warteschlange) setzt Titel an die Spitze.
  * **Aus Warteschlange entfernen** nimmt wartende Titel heraus; ihr Status wird auf den Stand vor dem Einreihen
    zurückgesetzt. „Wartende entfernen“ leert die Warteschlange, laufende Analysen werden noch beendet.
  * Doppelklick zeigt den Titel in der Liste; fertige Einträge lassen sich ausblenden.
* **Zuletzt analysiert:** Für jede Datei wird der Zeitpunkt der letzten *erfolgreichen* Analyse gespeichert und in
  Liste (Spalte „Zuletzt analysiert“) und Details angezeigt; fehlgeschlagene Analysen ändern ihn nicht.
* **Titel in andere Programme ziehen:** Ausgewählte Titel (auch mehrere) aus der Liste per Drag & Drop in einen
  Browser (z. B. Upload-Feld), einen Audioplayer, den Explorer oder DJ-Software ziehen – übergeben werden die
  Originaldateien. Zurück ins KeyTool-Fenster gezogen passiert nichts.
* **Kontextmenü der Tabelle:** erneut analysieren, Tags schreiben, im Explorer anzeigen, aus der Bibliothek entfernen
  (die Dateien selbst werden nie gelöscht).
* **Einstellungen:** Sprache, Darstellung, Notation, BPM-Bereich, automatisches Tag-Schreiben und welche Notation in
  den Tonart-Tag geschrieben wird (Standard: Kreuze, z. B. „F#m“ – kompatibel mit Rekordbox/Serato/Traktor).

### Analyse beschleunigen

* **Schnelles Tonart-Verfahren** (Einstellungen → Analyse → Tonart-Verfahren: *Schnell*): Die Trennung von
  Schlagzeug und harmonischem Anteil – rund 90 % der Rechenzeit – läuft auf dem Constant-Q-Spektrum statt auf dem
  vollen Spektrum. Etwa 4–5× schneller; in den Tests identische Ergebnisse. Vorher an eigenen Titeln prüfen:

  ```
  python -m keytool --compare-key D:\Musik --limit 25      (aus dem Quellcode)
  KeyTool-MCP.exe --compare-key D:\Musik --limit 25        (.exe-Version)
  ```

  Der Befehl analysiert eine über die Ordner verteilte Stichprobe mit beiden Verfahren, zeigt Abweichungen
  (Paralleltonart, Quinte, …), vergleicht beide mit vorhandenen Tonart-Tags (z. B. aus Mixed In Key) und gibt eine
  Empfehlung. Umschalten auch per `--set-key-method fast` bzw. `classic`.
* **Parallele Analysen:** automatisch bis zu 16 (ein Kern bleibt frei, ca. 1 GB RAM je Analyse); manuell in den
  Einstellungen.
* Titel mit BPM und Tonart in den Tags werden nicht analysiert, fehlt nur ein Wert, wird nur dieser berechnet.

### Hinweise zur Genauigkeit

* **BPM:** Onset-Analyse, Beat-Tracking und eine robuste Regression über alle Beats; bei Musik mit klarem Puls sehr
  zuverlässig. Halbe/doppelte Tempi werden in den eingestellten **BPM-Bereich** (Standard 70–180) gefaltet. Für
  Drum'n'Bass z. B. 85–175 oder 87–180 einstellen, für HipHop 60–120.
* **Tonart:** Harmonische Trennung (Schlagzeug wird ausgeblendet), Stimmungs-Korrektur (z. B. 432 Hz),
  Constant-Q-Chromagramm und ein Ensemble aus drei Tonart-Profilen (Krumhansl, Temperley, Shaath/KeyFinder).
  Wie bei allen Tools dieser Art (Mixed In Key, Rekordbox, KeyFinder) kommt es bei modaler oder atonaler Musik
  gelegentlich zu Verwechslungen mit der Paralleltonart oder der Quinte. Ein niedriger Sicherheitswert ist ein
  Hinweis zum Nachprüfen; Werte lassen sich manuell korrigieren.

## MCP-Server

Für Claude Desktop in `%APPDATA%\Claude\claude_desktop_config.json` eintragen
(den passenden Eintrag zeigt dir auch **Einstellungen → MCP-Server** zum Kopieren an):

```json
{
  "mcpServers": {
    "keytool": {
      "command": "C:\\Programme\\KeyTool\\KeyTool-MCP.exe"
    }
  }
}
```

Aus dem Quellcode: `"command": "C:\\Pfad\\zu\\KeyTool\\.venv\\Scripts\\python.exe", "args": ["-m", "keytool", "--mcp"]`.
Alternativ läuft der Server per `python -m keytool --mcp-http` (bzw. `KeyTool-MCP.exe --mcp-http`) über Streamable HTTP unter `http://127.0.0.1:8000/mcp` – nur lokal erreichbar.

GUI und MCP-Server nutzen dieselbe Datenbank (`%APPDATA%\KeyTool\library.sqlite3`) und können gleichzeitig laufen.

| Tool | Zweck |
|---|---|
| `search_tracks` | Suche mit allen Filtern, Sortierung, Paging, Notation wählbar |
| `get_track` | Alle Details eines Titels (per ID oder Pfad) |
| `compatible_tracks` | Harmonisch passende Titel (Camelot-Nachbarn, BPM-Toleranz) |
| `library_stats` | Anzahl, Status, BPM-Spanne, Tonart-Verteilung |
| `convert_key`, `list_all_keys` | Notationen umrechnen |
| `list_keywords`, `add_keywords`, `remove_keywords`, `set_keywords`, `rename_keyword` | Schlüsselwörter |
| `update_metadata` | Titel, Interpret, Album, Genre, Kommentar, Energy ändern (ein oder mehrere Titel) |
| `set_bpm`, `set_key`, `write_tags` | Manuelle Korrektur und Tags schreiben |
| `analyze_paths`, `reanalyze_tracks`, `get_job_status`, `cancel_job` | Dateien/Ordner einlesen und analysieren (Hintergrund-Job) |
| `search_karaoke_version` | Karaoke-Version nach Tonart(en)/BPM durchsuchen, optional mit Tempo/Tonarten/Dauer je Treffer |
| `remove_tracks`, `remove_missing_files` | Einträge aus der Bibliothek entfernen (Dateien bleiben erhalten) |

Beispiele für Anfragen an Claude: „Lies `D:\Musik\Neu` ein“, „Welche Tracks in 8A zwischen 120 und 126 BPM habe ich?“,
„Gib allen Titeln von Interpret X das Schlüsselwort Warmup“, „Was passt harmonisch zu Track 42?“

## Kommandozeile

```
python -m keytool                     # Oberfläche
python -m keytool --analyze D:\Musik  # einlesen & analysieren ohne Oberfläche
python -m keytool --mcp               # MCP-Server (stdio)
python -m keytool --compare-key D:\Musik [--limit 25]   # Tonart-Verfahren vergleichen
python -m keytool --set-key-method fast|classic        # Tonart-Verfahren wählen
```

Umgebungsvariablen: `KEYTOOL_DATA_DIR` (Ordner für Einstellungen + Datenbank), `KEYTOOL_DB` (Datenbankdatei).

## Projektstruktur

```
keytool/
  analysis.py       BPM- und Tonart-Erkennung (klassisch / schnell)
  analysis_queue.py Analyse-Warteschlange (Reihenfolge, „Jetzt analysieren“, absturzsicher)
  compare.py        Vergleich der Tonart-Verfahren an eigenen Titeln
  keys.py           Tonart-Modell, Notationen, Camelot-Rad
  tags.py           Metadaten lesen, BPM/Tonart in Tags schreiben
  db.py             SQLite-Bibliothek & Suche
  library.py        Einlesen, parallele Analyse, Tag-Schreiben
  mcp_server.py     MCP-Server
  research.py       Suche & Parser für Karaoke-Version
  serato.py         Serato-Cues/-Loops lesen (Serato Markers2)
  transpose.py      Transponieren ±1 Halbton bei Originaltempo (pedalboard/Rubber Band)
  waveform.py       Wellenform-Berechnung (3 Frequenzbänder) mit Cache
  i18n.py           Deutsch/Englisch
  gui/              Qt-Oberfläche (Hauptfenster, Camelot-Rad, Player, Details, Themes)
packaging/          PyInstaller-Spezifikation, Starter, Icon
tests/              Tests mit synthetisch erzeugten Audiodateien in allen Formaten
```

Tests: `pip install pytest` und `python -m pytest tests`.

## Lizenzhinweis

Das Transponieren nutzt [pedalboard](https://github.com/spotify/pedalboard) (GPLv3) mit der Rubber-Band-Bibliothek.
Für die private Nutzung ist das unproblematisch; wer KeyTool samt pedalboard weitergibt, muss die GPLv3 beachten.
