"""Decompose a Malayalam word into the sound units used by the unit inventory (see docs/MALAYALAM_SOUND_INVENTORY.md).

Units are written in Malayalam script, so they are unambiguous:
  syllable   a consonant with its vowel sign (ക, കാ, കി ...), optionally followed by anusvara / visarga
  vowel      a standalone vowel (അ, ആ ...)
  chillu     ൻ ർ ൽ ൾ ൺ ൿ
  hold       a consonant with chandrakkala that is the first part of a doubled / nasal+stop cluster (ക്, ന്, ത് ...)
With split_all every cluster becomes holds + a final syllable (ക്കു = ക് + കു), the smallest inventory (837 units for the
257k-word lexicon). decompose() returns None for text it cannot spell back exactly."""
import re
import unicodedata

CONS = "ക-ഺൎ"
VOW = "അ-ഔൠൡ"
SIGN = "ാ-ൄെ-ൈൊ-ൌൗ"
CHILLU = "ൺ-ൿ"
VIR = "്"
ZWJ, ZWNJ = "‍", "‌"
AKS = re.compile("(?:[%s](?:%s[%s])*(?:[%s]|%s)?|[%s]|[%s])[ംഃ]?" % (CONS, VIR, CONS, SIGN, VIR, VOW, CHILLU))
OLD_CHILLU = {"ണ" + VIR + ZWJ: "ൺ", "ന" + VIR + ZWJ: "ൻ", "ര" + VIR + ZWJ: "ർ", "ല" + VIR + ZWJ: "ൽ", "ള" + VIR + ZWJ: "ൾ", "ക" + VIR + ZWJ: "ൿ"}
LIQ = set("യരലവ")
_SPLIT = re.compile("([%s](?:%s[%s])+)([%s])?([ംഃ])?" % (CONS, VIR, CONS, SIGN))


def normalise(w):
    w = unicodedata.normalize("NFC", w)
    for k, v in OLD_CHILLU.items():
        w = w.replace(k, v)
    w = w.replace(ZWJ, "").replace(ZWNJ, "")
    for a, b in (("െെ", "ൈ"), ("ൊ", "ൊ"), ("ോ", "ോ"), ("ൌ", "ൌ")):
        w = w.replace(a, b)
    return w


def split_akshara(u, split_all=True):
    m = _SPLIT.fullmatch(u)
    if not m:
        return [u]
    cons = [p for p in m.group(1).split(VIR) if p]
    tail = (m.group(2) or "") + (m.group(3) or "")
    if not split_all and len(cons) == 2 and cons[1] in LIQ:
        return [u]
    return [c + VIR for c in cons[:-1]] + [cons[-1] + tail]


def kind(u):
    if re.fullmatch("[%s][ംഃ]?" % CHILLU, u):
        return "chillu"
    if re.fullmatch("[%s][ംഃ]?" % VOW, u):
        return "vowel"
    if re.fullmatch("[%s]%s" % (CONS, VIR), u):
        return "hold"
    if re.fullmatch("[%s](?:[%s])?[ംഃ]?" % (CONS, SIGN), u):
        return "syllable"
    return "cluster"


def decompose(word, split_all=True):
    """-> list of unit strings whose concatenation equals normalise(word), or None."""
    n = normalise(word)
    segs = AKS.findall(n)
    if not n or "".join(segs) != n:
        return None
    return [x for s in segs for x in split_akshara(s, split_all)]
