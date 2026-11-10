# KeyTool – key & BPM analysis with a music library and MCP server

KeyTool analyses audio files (MP3, WAV, FLAC, OGG, M4A), detects **tempo (BPM)** and **key** and keeps everything in
a searchable library. You can search and filter by title, artist, album, year, BPM, key and your own **keywords**.
Through the built-in **MCP server**, Claude Desktop and similar apps can work with the library directly.

## Features

| Area | Scope |
|---|---|
| Formats | MP3, WAV, FLAC, OGG (Vorbis/Opus), M4A/AAC/ALAC – decoded with FFmpeg (included in PyAV) |
| Import | Single files, whole folder trees (recursive), **drag & drop from Windows Explorer** |
| Analysis | BPM with one decimal and octave correction, key (24 major/minor), confidence for each result |
| Notations | **Camelot** (8A), **sharps** (F#m), **flats** (Gbm), plus Open Key (1m) |
| Search | Free text + title, artist, album, year from/to, BPM from/to, keys (multiple), keywords (combined with "or" by default, switchable to "and") |
| Keywords | Per track or for many tracks at once; rename and remove everywhere |
| DJ extras | "Harmonic match": tracks that fit the selection on the Camelot wheel (±6 % BPM); halve/double BPM |
| Analyse only what is missing | If only the key is in the tags, only the BPM is analysed (about 20× faster than a full analysis); if only the BPM is there, only the key; if both are there, nothing |
| Tags | BPM & key are **written into the file automatically** after analysis (ID3 TBPM/TKEY, Vorbis BPM/INITIALKEY, MP4 tmpo/initialkey) – can be turned off |
| Details | Right-hand sidebar, shown/hidden with "Details" in the toolbar or Ctrl+I (remembered). Edit title, artist, album, album artist (ALBUMARTIST, only shown here), year, genre, comment, key, energy (1–10) and tempo – for one track or many at once; file type, bitrate, file size, duration and path are shown. Changes are written into the file tags (can be turned off) |
| Manual correction | Override BPM/key; manual values are kept when a track is analysed again |
| Research | Search **Karaoke-Version** (karaoke-version.de) by key(s) and BPM range (see the note on bot protection below) |
| Player (deck) | Full-width deck: large title, key chip in its Camelot colour, large tempo, transpose buttons with the target key (e.g. "+1 3A"); cover (embedded or cover.jpg/folder.jpg in the folder, otherwise a placeholder), previous/next track, play/pause, stop, continuous play; plays through the system's default output device (follows device changes, e.g. headphones), volume, mute |
| Transpose | Play one semitone higher (+1) or lower (−1) at the original tempo (Rubber Band via pedalboard); positions and cue points stay valid |
| Serato cues | Existing Serato DJ hot cues (colour, name) and saved loops are read from the files (MP3, WAV, FLAC, M4A; OGG where possible) and shown in the waveform; clicking a cue flag or Alt+1…8 jumps to it |
| Waveform | Overview of the deck track, coloured by frequency band (bass blue, mids amber, highs white), click/drag to seek, bar ticks from the BPM |
| Fits the deck | "Fits" column (● same key, ◐ harmonic neighbour ±1 or relative major/minor, ○ does not fit, transposition included) and "vs. deck" (tempo difference in %, dimmed beyond ±6 %); both sortable |
| User interface | English, German, Spanish, French, Italian (in the settings; numbers, dates and key names such as "a-Moll" / "La menor" / "la mineur" follow the language), light and dark mode (or system), switch without restarting; Archivo typeface (SIL OFL, bundled), colour only for keys, amber for playback |
| MCP server | Full access: search, details, keywords, set values, write tags, import & analyse folders |

## Installation & start (Windows)

**Option A – from the source code:**
1. Install Python 3.10–3.13 (64 bit) from python.org (tick "Add to PATH").
2. Double-click `start.bat`. The first start sets up a virtual environment (takes a few minutes).
   When a new version adds packages, `start.bat` installs them automatically on the next start.

**Option B – build your own .exe:**
1. Double-click `build_exe.bat`.
2. Result: `dist\KeyTool\KeyTool.exe` (app) and `dist\KeyTool\KeyTool-MCP.exe` (MCP server).
   You can copy the whole `dist\KeyTool` folder anywhere.

A separate FFmpeg installation is not needed.

**Option C – GitHub Actions:** `.github/workflows/build.yml` builds both executables on a Windows runner for every
push and pull request to `main`: tests → PyInstaller → smoke test (`packaging/smoke_test.py` analyses a synthetic
track with the built `KeyTool-MCP.exe` and checks BPM and key) → zip as a workflow artifact
(`KeyTool-<commit>-windows-x64.zip`, kept 30 days). Pushing a tag such as `v1.1.0` also creates a GitHub release
with `KeyTool-v1.1.0-windows-x64.zip` attached. It can also be started by hand under Actions → "Build Windows
executables" → Run workflow.

## Usage

* **Import files/folders:** toolbar, or drag them from Explorer into the window. Files that were already analysed
  and have not changed are skipped. Analysis runs in parallel on several CPU cores.
* **Search:** the "Search everything" field (Ctrl+F) searches title, artist, album, file name, genre, year and
  keywords. All filters on the left can be combined.
* **Key filter:** the coloured segments are the Camelot wheel; the labels follow the chosen notation.
* **Keywords:** select tracks (multiple selection possible), type on the right, separate several with commas.
  Double-clicking a keyword filters by it. Right-click in the filter list: rename/remove.
* **Edit details:** change fields on the right and press "Save" (Ctrl+S) or "Discard". With several tracks selected,
  differing values show "(multiple values)"; only the fields you change are applied. If you switch tracks with
  unsaved changes, KeyTool asks. Energy is stored as `TXXX:EnergyLevel` (MP3/WAV), `ENERGYLEVEL` (FLAC/OGG) or
  `EnergyLevel` (M4A); Mixed In Key comments such as "8A - Energy 6" are recognised on import.
* **Play:** double-click (or Enter) loads a track into the deck and plays it; a single click only shows the details.
  Space starts/pauses, clicking or dragging in the waveform seeks. If the deck track is analysed again or edited,
  the deck shows the new values right away. ⏮/⏭ (Ctrl+←/→ or media keys) go from the deck track to the
  previous/next one in the current sort order and filter; after the first 3 seconds ⏮ restarts the track first.
  At the end of a track the next one plays; at the end of the list the player stops. Clicking the cover enlarges it.
* **Transpose:** choose "−1 / 0 / +1" in the player. The track keeps playing while the transposed version is
  computed in the background (on all CPU cores; progress next to the time), then playback switches seamlessly at
  the same position. The tempo stays exactly the same; the display shows the new key (e.g. "8A → 3A (+1)"). The
  setting also applies to the next tracks; computed versions are cached (the last 12, in the data folder under
  `transpose_cache`). The original file is never changed.
* **Serato cues:** hot cues appear in the waveform as a blue line (#08599B) with a numbered flag, saved loops as a shaded
  range (🔒 = locked). Clicking a line or flag (or Alt+1…8) sets the playback position to the cue with millisecond
  precision – also during playback and while the track is still loading. Tooltips show name and time.
  The buttons left and right of play/pause jump to the previous / next cue point (in time order); they are
  disabled when the track has no cue points. "Previous" within 0.5 s after a cue goes one cue further back. KeyTool reads
  the "Serato Markers2" tag (format description: Holzhaus/serato-tags) and never changes it – not even when writing
  BPM, key or metadata. If tags are written during playback, the player releases the file briefly and continues at
  the same position.
* **Karaoke-Version research** ("Search Karaoke-Version" in the toolbar or right-click "Find matching tracks on
  Karaoke-Version"): choose keys on the Camelot wheel ("Add harmonic keys" adds the neighbours, a click in the centre clears all keys – as in the main window), set a BPM range and
  click "Search in browser". The dialog is prefilled with the current key/BPM filter or with the key and BPM (±3 %)
  of the selected track, and shows which keys and which tempo range the search covers.
  * Karaoke-Version only serves search results to real web browsers (bot protection, Fastly "Client Challenge",
    since November 2026). KeyTool does not work around this and no longer fetches results itself: it composes one
    search with all chosen keys and the BPM range and opens it in your default browser.
  * **Include ±1 semitone** (on by default): the search also covers the keys one semitone lower and higher – such
    tracks can be transposed to fit on Karaoke-Version.
  * The MCP tool `search_karaoke_version` still tries the site directly; while the bot protection is active it
    returns `error: blocked_by_bot_protection` and the link `search_url`.
* **Import from Mixed In Key** ("Analyze ▾ → Import from Mixed In Key …"): reads the library of Mixed In Key 10/11
  (`%LOCALAPPDATA%\Mixed In Key\Mixed In Key\<version>\MIKStore.db`, found automatically) and takes over key, tempo
  and energy. Tracks are matched by file path, moved files by their unique file name. A preview is shown before the
  import; options: replace existing values or only fill empty fields (manual values are always kept), Mixed In Key
  collections as keywords, add missing tracks. Imported values count like values from the tags: a later analysis
  does not overwrite them, "Re-analyze" does. Mixed In Key itself is not changed (the database is copied before
  reading). Without the UI: `KeyTool-MCP.exe --import-mik [MIKStore.db] [--add-missing] [--fill-only] [--no-keywords]`.
* **Analysis queue** ("Queue" in the toolbar, opens only when clicked; while analyses run or wait, the button is
  highlighted in amber and shows the count): all analyses – import, "Analyze pending", "Re-analyze" – go into a
  queue that is worked through from top to bottom (with several parallel analyses the topmost waiting tracks always
  start first; with "Parallel analyses = 1" strictly one after another).
  * **Analyse now** (right-click in the list or in the queue) moves tracks to the top.
  * **Remove from queue** takes waiting tracks out; their status returns to what it was before they were queued.
    "Remove waiting" empties the queue; running analyses still finish.
  * Double-click shows the track in the list; finished entries can be hidden.
* **Last analysed:** for every file the time of the last *successful* analysis is stored and shown in the list
  (column "Last analysed") and the details; failed analyses do not change it.
* **Drag tracks into other apps:** drag selected tracks (also several) from the list into a browser (e.g. an
  upload field), an audio player, Explorer or DJ software – the original files are passed on. Dragging them back
  into the KeyTool window does nothing.
* **Context menu of the list:** play, load into deck, filter by artist / album / key, re-analyse, write tags, show
  in Explorer, remove from library (the files themselves are never deleted).
* **Settings:** language, colour scheme, notation, BPM range, analysis method, automatic tag writing and which
  notation goes into the key tag (default: sharps, e.g. "F#m" – compatible with rekordbox/Serato/Traktor), MCP.

### Faster analysis

* **Fast key method** (Settings → Analysis → Key detection method: *Fast*): separating drums from the harmonic part
  – about 90 % of the computing time – runs on the constant-Q spectrum instead of the full spectrum. About 4–5×
  faster; identical results in the tests. Check it on your own tracks first:

  ```
  python -m keytool --compare-key D:\Music --limit 25      (from the source code)
  KeyTool-MCP.exe --compare-key D:\Music --limit 25        (.exe version)
  ```

  The command analyses a sample spread over the folders with both methods, shows differences (relative key,
  fifth, …), compares both with existing key tags (e.g. from Mixed In Key) and gives a recommendation. You can
  also switch with `--set-key-method fast` or `classic`.
* **Parallel analyses:** automatically up to 16 (one core stays free, about 1 GB RAM per analysis); manually in the
  settings.
* Tracks with BPM and key in their tags are not analysed; if only one value is missing, only that one is computed.

### Notes on accuracy

* **BPM:** onset analysis, beat tracking and a robust regression over all beats; very reliable for music with a
  clear pulse. Half/double tempi are folded into the configured **BPM range** (default 70–180). For drum'n'bass set
  e.g. 85–175 or 87–180, for hip-hop 60–120.
* **Key:** harmonic separation (drums are removed), tuning correction (e.g. 432 Hz), constant-Q chromagram and an
  ensemble of three key profiles (Krumhansl, Temperley, Shaath/KeyFinder). As with all tools of this kind (Mixed In
  Key, rekordbox, KeyFinder), modal or atonal music is occasionally confused with the relative key or the fifth. A
  low confidence is a hint to double-check; values can be corrected manually.

## MCP server

For Claude Desktop, add this to `%APPDATA%\Claude\claude_desktop_config.json`
(**Settings → MCP server** shows the matching entry to copy):

```json
{
  "mcpServers": {
    "keytool": {
      "command": "C:\\Program Files\\KeyTool\\KeyTool-MCP.exe"
    }
  }
}
```

From the source code: `"command": "C:\\path\\to\\KeyTool\\.venv\\Scripts\\python.exe", "args": ["-m", "keytool", "--mcp"]`.
Alternatively the server runs with `python -m keytool --mcp-http` (or `KeyTool-MCP.exe --mcp-http`) over streamable
HTTP at `http://127.0.0.1:8000/mcp` – reachable only locally.

The app and the MCP server share the same database (`%APPDATA%\KeyTool\library.sqlite3`) and can run at the same
time.

**More languages:** the English and German texts are in `keytool/i18n.py`, further languages each in
`keytool/locales/<code>.py` (es, fr, it). A new language = a new file + an entry in `LANGUAGES`; missing texts
appear in English, and `tests/test_i18n.py` reports gaps and placeholders that differ.

**Troubleshooting:** if an analysis fails or an analysis process crashes, the cause (Python traceback, or crash
location and exit code) is in `%APPDATA%\KeyTool\analysis.log`. The .exe without a console window also writes
error messages there that would otherwise stay invisible.

| Tool | Purpose |
|---|---|
| `search_tracks` | Search with all filters, sorting, paging, choice of notation |
| `get_track` | All details of a track (by ID or path) |
| `compatible_tracks` | Harmonically matching tracks (Camelot neighbours, BPM tolerance) |
| `library_stats` | Count, status, BPM range, key distribution |
| `convert_key`, `list_all_keys` | Convert notations |
| `list_keywords`, `add_keywords`, `remove_keywords`, `set_keywords`, `rename_keyword` | Keywords |
| `import_mixed_in_key` | Take over key, tempo, energy and collections from Mixed In Key (`dry_run` for a preview only) |
| `update_metadata` | Change title, artist, album, album artist, year, genre, comment, energy (one or more tracks) |
| `set_bpm`, `set_key`, `write_tags` | Manual correction and writing tags |
| `analyze_paths`, `reanalyze_tracks`, `get_job_status`, `cancel_job` | Import and analyse files/folders (background job) |
| `search_karaoke_version` | Search Karaoke-Version by key(s)/BPM, optionally with tempo/keys/duration per hit and ±1 semitone (`include_semitones`) |
| `remove_tracks`, `remove_missing_files` | Remove entries from the library (the files are kept) |

Example requests to Claude: "Import `D:\Music\New`", "Which tracks in 8A between 120 and 126 BPM do I have?",
"Give all tracks by artist X the keyword Warmup", "What fits harmonically with track 42?"

## Command line

```
python -m keytool                     # app
python -m keytool --analyze D:\Music  # import & analyse without the UI
python -m keytool --mcp               # MCP server (stdio)
python -m keytool --compare-key D:\Music [--limit 25]   # compare the key detection methods
python -m keytool --set-key-method fast|classic        # choose the key detection method
python -m keytool --import-mik [MIKStore.db] [--add-missing] [--fill-only] [--no-keywords]   # Mixed In Key import
```

Environment variables: `KEYTOOL_DATA_DIR` (folder for settings + database), `KEYTOOL_DB` (database file).

## Project structure

```
keytool/
  analysis.py       BPM and key detection (classic / fast)
  analysis_queue.py Analysis queue (order, "Analyse now", crash-safe)
  compare.py        Compare the key detection methods on your own tracks
  keys.py           Key model, notations, Camelot wheel
  tags.py           Read metadata, write BPM/key and edits into tags
  db.py             SQLite library & search
  library.py        Import, parallel analysis, tag writing, analysis.log
  mik_import.py     Import from Mixed In Key (MIKStore.db)
  mcp_server.py     MCP server
  research.py       Search & parser for Karaoke-Version
  serato.py         Read Serato cues/loops (Serato Markers2)
  transpose.py      Transpose ±1 semitone at the original tempo (pedalboard/Rubber Band)
  waveform.py       Waveform computation (3 frequency bands) with cache
  i18n.py           UI texts (English, German) and formatting of numbers/dates
  locales/          Further UI languages (es, fr, it)
  gui/              Qt user interface (main window, deck, Camelot wheel, details, dialogs, themes)
packaging/          PyInstaller spec, launchers, icon, smoke test for the built .exe
.github/workflows/  CI: tests, Windows build, release
tests/              Tests with synthetic audio files in all formats
```

Tests: `pip install pytest` and `python -m pytest tests`.

## Licence note

Transposing uses [pedalboard](https://github.com/spotify/pedalboard) (GPLv3) with the Rubber Band library. That is
fine for private use; anyone who distributes KeyTool together with pedalboard has to observe the GPLv3.
