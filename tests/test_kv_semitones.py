"""Karaoke-Version search including keys 1 semitone below/above (transposable on the website)."""
from keytool.keys import Key
from keytool.research import semitone_shift, with_semitones


def cam(keys):
    return [k.camelot() for k in keys]


def test_with_semitones():
    assert cam(with_semitones([Key.parse("8A")])) == ["8A", "1A", "3A"]          # Am + G#m + Bbm
    both = with_semitones([Key.parse("8A"), Key.parse("1A")])                    # overlapping neighbours
    assert cam(both) == ["8A", "1A", "3A", "6A"]                                 # shared neighbour only once


def test_semitone_shift():
    target = {Key.parse("8A").code}
    assert semitone_shift([Key.parse("8A").code], target) == 0
    assert semitone_shift([Key.parse("1A").code], target) == 1                   # G#m +1 -> Am
    assert semitone_shift([Key.parse("3A").code], target) == -1                  # Bbm -1 -> Am
    assert semitone_shift([Key.parse("8B").code], target) is None                # other mode: no
    assert semitone_shift([Key.parse("3A").code, Key.parse("8A").code], target) == 0   # key change: 0 wins

