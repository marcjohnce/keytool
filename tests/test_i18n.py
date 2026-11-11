"""Every UI text exists in French and Italian with the same placeholders; numbers, dates and key names
follow the UI language."""
import string

import pytest

from keytool import i18n
from keytool.i18n import LANGUAGES, _T, fmt_datetime, fmt_number, set_language, strings_for, tr
from keytool.keys import Key


def _fields(s: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


@pytest.mark.parametrize("lang", ["es", "fr", "it"])
def test_locale_complete_with_same_placeholders(lang):
    texts = strings_for(lang)
    assert set(texts) == set(_T), (sorted(set(_T) - set(texts)), sorted(set(texts) - set(_T)))
    for key, (de, en) in _T.items():
        assert _fields(texts[key]) == _fields(en), key
        assert texts[key].strip(), key
        assert "key" not in _fields(texts[key]), f"{key}: placeholder {{key}} clashes with tr(key, ...)"


def test_languages_and_fallback(monkeypatch):
    assert set(LANGUAGES) == {"de", "en", "es", "fr", "it"}
    try:
        set_language("fr")
        assert tr("add_files") == "Ajouter des fichiers"
        monkeypatch.setitem(i18n._extra, "fr", {})          # a missing French text falls back to English
        assert tr("add_files") == "Add files"
        set_language("it")
        assert tr("tracks_count", shown=3, total=9) == "3 di 9 brani"
        set_language("xx")                                   # unknown code -> English
        assert tr.lang == "en"
    finally:
        set_language("de")


def test_numbers_dates_and_key_names():
    try:
        for lang, num in (("de", "124,5"), ("es", "124,5"), ("fr", "124,5"), ("it", "124,5"), ("en", "124.5")):
            set_language(lang)
            assert fmt_number(124.5) == num
        ts = 1700000000
        set_language("fr"); assert fmt_datetime(ts)[2] == "/" and fmt_datetime(ts)[5] == "/"
        set_language("es"); assert fmt_datetime(ts)[2] == "/"
        set_language("de"); assert fmt_datetime(ts)[2] == "."
        set_language("en"); assert fmt_datetime(ts)[4] == "-"
        assert fmt_datetime(None) == ""
    finally:
        set_language("de")
    k = Key.parse("4B")                                      # Ab major
    assert (k.long_name("fr"), k.long_name("it"), k.long_name("es")) == \
        ("la bémol majeur", "La bemolle maggiore", "La bemol mayor")
    assert Key.parse("12A").long_name("es") == "Do sostenido menor"
    k = Key.parse("8A")
    assert (k.long_name("de"), k.long_name("fr"), k.long_name("it")) == ("a-Moll", "la mineur", "La minore")


def test_no_duplicate_keys_in_string_tables():
    """A key defined twice silently overrides the first one (that hid a new menu text once)."""
    import ast
    import collections
    from pathlib import Path
    root = Path(i18n.__file__).parent
    for f in [root / "i18n.py", *sorted((root / "locales").glob("*.py"))]:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Dict) and len(node.keys) > 50:
                c = collections.Counter(k.value for k in node.keys if isinstance(k, ast.Constant))
                assert not [k for k, n in c.items() if n > 1], f.name
