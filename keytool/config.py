"""Paths and persistent settings (shared by GUI and MCP server)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field, fields
from pathlib import Path

APP_NAME = "KeyTool"


def _default_export_columns():
    from .export import DEFAULT_COLUMNS
    return DEFAULT_COLUMNS


def _default_patterns() -> list[str]:
    from .retag import DEFAULT_PATTERNS
    return DEFAULT_PATTERNS


def data_dir() -> Path:
    override = os.environ.get("KEYTOOL_DATA_DIR")
    if override:
        p = Path(override)
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        p = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "keytool"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return Path(os.environ.get("KEYTOOL_DB", data_dir() / "library.sqlite3"))


@dataclass
class Settings:
    language: str = "en"            # en | de | es | fr | it
    theme: str = "dark"             # dark | light | system
    notation: str = "camelot"       # camelot | sharps | flats | openkey
    bpm_min: float = 70.0
    bpm_max: float = 180.0
    use_existing_tags: bool = True  # take BPM/key from file tags instead of analysing
    write_edits: bool = True        # write edited title/artist/album/genre/comment/energy into the files
    write_tags: bool = True         # write BPM/key into files after analysis
    tag_key_notation: str = "sharps"  # notation used when writing the key tag
    workers: int = 0                # 0 = auto (CPU count - 1, max 16, ~1 GB RAM per analysis)
    key_method: str = "classic"     # "classic" | "fast" (CQT-domain HPSS, ~4x faster) - see --compare-key
    reanalyze_existing: bool = False
    detect_sections: bool = True    # detect song sections with every analysis (menu Analyse works anyway)
    # CSV export (see export.py): columns in file order, delimiter, ask for the file or use folder + name
    export_columns: list = field(default_factory=lambda: list(_default_export_columns()))
    export_delimiter: str = "auto"  # auto | comma | semicolon | tab
    export_ask: bool = True         # ask for the file name with every export
    export_folder: str = ""         # start folder / fixed folder ("" = Documents)
    export_filename: str = "KeyTool-{date}.csv"   # {date} {time} {count}
    volume: float = 0.8             # player volume 0..1
    show_details: bool = True       # right sidebar (metadata, file info, keywords) visible
    show_chat: bool = False         # AI chat sidebar visible
    chat_provider: str = "anthropic"  # anthropic | openai (any OpenAI-compatible endpoint, e.g. Ollama)
    chat_model: str = ""            # "" = default model of the provider (assistant.DEFAULT_MODEL)
    chat_base_url: str = ""         # OpenAI-compatible endpoint ("" = Ollama on this computer)
    research_semitones: bool = True      # also search keys 1 semitone below/above (transposable on KV)
    research_bpm_tolerance: float = 3.0  # +-% around the selected track's BPM
    # Auto re-tag: regular expressions for file names, first match wins (see retag.match_patterns)
    retag_patterns: list = field(default_factory=lambda: list(_default_patterns()))

    @classmethod
    def load(cls) -> "Settings":
        p = data_dir() / "settings.json"
        s = cls()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                names = {f.name for f in fields(cls)}
                for k, v in raw.items():
                    if k in names:
                        setattr(s, k, v)
            except Exception:
                pass
        return s

    def save(self) -> None:
        p = data_dir() / "settings.json"
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def worker_count(self) -> int:
        if self.workers and self.workers > 0:
            return int(self.workers)
        # Auto: one core left for the UI, at most 16, and ~1 GB RAM per analysis process
        n = min(16, max(1, (os.cpu_count() or 2) - 1))
        ram = total_ram_bytes()
        if ram:
            n = min(n, max(1, int(ram * 0.6 / 1_000_000_000)))
        return n


def total_ram_bytes() -> int | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullTotalPhys)
            return None
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except Exception:
        return None
